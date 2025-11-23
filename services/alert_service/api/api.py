"""
Alert Service REST API
Schema-per-tenant architecture with JWT authentication
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from prometheus_fastapi_instrumentator import Instrumentator
import asyncpg
from datetime import datetime
from config import Config

# Import routers
from routers.alert_rules_router import router as alert_rules_router
from routers.ml_models_router import router as ml_models_router
from routers.alerts_history_router import router as alerts_history_router

# Database connection pool (global)
db_pool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - startup and shutdown"""
    global db_pool
    
    # Validate configuration
    Config.validate()
    
    # Startup: Initialize database pool
    print("🔌 Connecting to database...")
    db_pool = await asyncpg.create_pool(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        user=Config.ALERT_SERVICE_DB_USER,
        password=Config.ALERT_SERVICE_DB_PASSWORD,
        database=Config.DB_NAME,
        min_size=Config.DB_MIN_SIZE,
        max_size=Config.DB_MAX_SIZE
    )
    print(f"✅ Database pool created: {Config.DB_MIN_SIZE}-{Config.DB_MAX_SIZE} connections")
    
    # Store db_pool in app state for access in routes
    app.state.db_pool = db_pool
    
    yield  # Application runs here
    
    # Shutdown: Close database pool
    print("🔌 Closing database pool...")
    await db_pool.close()
    print("✅ Database pool closed")


app = FastAPI(
    title="IndustryFlow Alert Service API",
    description="Alert rules management and ML model API - Schema-per-tenant architecture",
    version="2.0.0",
    lifespan=lifespan
)

# CORS configuration
cors_origins = Config.CORS_ORIGINS.split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Include routers
app.include_router(alert_rules_router)
app.include_router(ml_models_router)
app.include_router(alerts_history_router)


@app.get("/")
async def root():
    return {
        "service": "IndustryFlow Alert Service API",
        "version": "2.0.0",
        "architecture": "schema-per-tenant",
        "endpoints": {
            "health": "/health",
            "alert_rules": "/api/alert-rules",
            "ml_models": "/api/ml-models",
            "alerts": "/api/alerts"
        }
    }


@app.get("/health")
async def health_check():
    """Health check with database connectivity test"""
    db_status = "disconnected"
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                if result == 1:
                    db_status = "connected"
        except Exception as e:
            db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy",
        "database": db_status,
        "architecture": "schema-per-tenant"
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "alert-api",
        "pool_size": db_pool.get_size() if db_pool else 0,
        "pool_free": db_pool.get_idle_size() if db_pool else 0
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=Config.API_PORT)
