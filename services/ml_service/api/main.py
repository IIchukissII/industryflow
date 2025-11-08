"""
MLOps FastAPI Service - Main Application
Provides model management and MLflow integration endpoints
Schema-per-tenant architecture with dual database connections
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncpg
import logging
from pathlib import Path

import config
from repository import MLRepository, MLflowRepository
from routers import health_router, models_router, mlflow_router

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
    version="2.0.0",
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


# ============================================================================
# Include Routers
# ============================================================================

app.include_router(health_router)
app.include_router(models_router)
app.include_router(mlflow_router)


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
    
    # Create connection pool for industryflow database
    try:
        app.state.db_pool = await asyncpg.create_pool(
            host=config.config.DB_HOST,
            port=int(config.config.DB_PORT),
            database=config.config.DB_NAME,
            user=config.config.ML_SERVICE_DB_USER,
            password=config.config.ML_SERVICE_DB_PASSWORD,
            min_size=5,
            max_size=20,
            command_timeout=60
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
            min_size=5,
            max_size=20,
            command_timeout=60
        )
        logger.info(f"MLflow pool created: {config.config.MLFLOW_DB_USER}@{config.config.DB_HOST}/{config.config.MLFLOW_DB_NAME}")
    except Exception as e:
        logger.error(f"Failed to create MLflow database pool: {e}")
        raise
    
    # Create repository instances
    app.state.ml_repository = MLRepository(app.state.db_pool)
    app.state.mlflow_repository = MLflowRepository(app.state.mlflow_pool)
    logger.info("Repositories initialized")
    
    logger.info(f"Model directory: {MODEL_DIR}")
    logger.info(f"Existing models: {len(list(MODEL_DIR.glob('*.pkl')))}")
    logger.info(f"MLflow Tracking URI: {config.config.MLFLOW_TRACKING_URI}")
    logger.info("MLOps API startup complete")
    logger.info("="*60)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("MLOps API shutting down...")
    
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
