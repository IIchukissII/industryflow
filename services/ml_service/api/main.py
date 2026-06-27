# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
MLOps FastAPI Service - Main Application
Provides model management and MLflow integration endpoints
Schema-per-tenant architecture with dual database connections
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
import asyncpg
import logging
import secrets
from pathlib import Path

# Auth cookie names — must match the gateway's CookieTransport / CSRF cookie (ADR-0004 dec 3).
ACCESS_COOKIE = "if_access"
CSRF_COOKIE = "if_csrf"

import config
from repository import MLRepository, MLflowRepository
from routers import health_router, models_router, mlflow_router, inference_router, feature_configs_router, training_router
from feature_engineering import init_feature_store

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Model directory
MODEL_DIR = Path(config.config.MODEL_DIR)
MODEL_DIR.mkdir(exist_ok=True)

# Create FastAPI app
app = FastAPI(
    title="IndustryFlow MLOps Service",
    description="Machine Learning Operations API with schema-per-tenant isolation",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.config.CORS_ORIGINS.split(','),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def csrf_protect(request, call_next):
    """
    Double-submit CSRF protection for cookie-authenticated browser requests (ADR-0004
    dec 3), mirroring the gateway. Enforced only on unsafe methods when the request
    carries the access cookie and no bearer header — bearer/internal-token service
    clients (e.g. the alert worker calling /api/inference) are not CSRF-vulnerable.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        cookie_authed = ACCESS_COOKIE in request.cookies and not request.headers.get("authorization")
        if cookie_authed:
            csrf_cookie = request.cookies.get(CSRF_COOKIE)
            csrf_header = request.headers.get("x-csrf-token")
            if not (csrf_cookie and csrf_header and secrets.compare_digest(csrf_cookie, csrf_header)):
                return JSONResponse(status_code=403, content={"detail": "CSRF token missing or invalid"})
    return await call_next(request)


# Prometheus metrics
Instrumentator().instrument(app).expose(app)


# ============================================================================
# Include Routers
# ============================================================================

app.include_router(health_router)
app.include_router(models_router)
app.include_router(mlflow_router)
app.include_router(inference_router)
app.include_router(feature_configs_router)
app.include_router(training_router)


# ============================================================================
# Startup/Shutdown Events
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize ML service on startup"""
    logger.info("="*60)
    logger.info("MLOps API starting up...")
    
    # Validate configuration
    try:
        config.config.validate()
    except RuntimeError as e:
        logger.error(f"Configuration validation failed: {e}")
        raise

    # Load configured extension plugins (ADR-0010): registers their feature transforms.
    from extensions import load_extension_modules, registered_transforms, EXTENSION_API_VERSION
    modules = [m for m in config.config.EXTENSION_MODULES.split(",") if m.strip()]
    loaded = load_extension_modules(modules)
    logger.info(f"Extension API {EXTENSION_API_VERSION}; modules: {loaded or 'none'}; "
                f"transforms: {registered_transforms()}")

    # Create connection pool for industryflow database
    try:
        app.state.db_pool = await asyncpg.create_pool(
            host=config.config.DB_HOST,
            port=int(config.config.DB_PORT),
            database=config.config.DB_NAME,
            user=config.config.ML_SERVICE_DB_USER,
            password=config.config.ML_SERVICE_DB_PASSWORD,
            min_size=2,
            max_size=4,
            command_timeout=60,
            ssl=config.db_ssl_context(),
        )
        logger.info(f"Database pool created: {config.config.ML_SERVICE_DB_USER}@{config.config.DB_HOST}/{config.config.DB_NAME}")
    except Exception as e:
        logger.error(f"Failed to create database pool: {e}")
        raise
    
    # Create connection pool for mlflow database
    try:
        app.state.mlflow_pool = await asyncpg.create_pool(
            host=config.config.DB_HOST,
            port=int(config.config.DB_PORT),
            database=config.config.MLFLOW_DB_NAME,
            user=config.config.MLFLOW_DB_USER,
            password=config.config.MLFLOW_DB_PASSWORD,
            min_size=2,
            max_size=4,
            command_timeout=60,
            ssl=config.db_ssl_context(),
        )
        logger.info(f"MLflow pool created: {config.config.MLFLOW_DB_USER}@{config.config.DB_HOST}/{config.config.MLFLOW_DB_NAME}")
    except Exception as e:
        logger.error(f"Failed to create MLflow database pool: {e}")
        raise
    
    # Create repository instances
    app.state.ml_repository = MLRepository(app.state.db_pool)
    app.state.mlflow_repository = MLflowRepository(app.state.mlflow_pool)
    logger.info("Repositories initialized")

    # Initialize Feature Store
    try:
        redis_url = getattr(config.config, 'REDIS_URL', 'redis://redis:6379/0')
        app.state.feature_store = await init_feature_store(
            redis_url=redis_url,
            max_window=100,
            ttl_seconds=3600
        )
        logger.info(f"Feature Store initialized: {redis_url}")
    except Exception as e:
        logger.error(f"Failed to initialize Feature Store: {e}")
        raise

    logger.info(f"Model directory: {MODEL_DIR}")
    logger.info(f"Existing models: {len(list(MODEL_DIR.glob('*.pkl')))}")
    logger.info(f"MLflow Tracking URI: {config.config.MLFLOW_TRACKING_URI}")
    logger.info("MLOps API startup complete")
    logger.info("="*60)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("MLOps API shutting down...")

    # Close Feature Store
    if hasattr(app.state, 'feature_store'):
        await app.state.feature_store.close()
        logger.info("Feature Store closed")

    # Close database pools
    if hasattr(app.state, 'db_pool'):
        await app.state.db_pool.close()
        logger.info("Database pool closed")

    if hasattr(app.state, 'mlflow_pool'):
        await app.state.mlflow_pool.close()
        logger.info("MLflow pool closed")

    logger.info("Shutdown complete")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
