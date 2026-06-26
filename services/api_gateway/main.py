# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from prometheus_fastapi_instrumentator import Instrumentator

from config import get_settings
from routers import (
    health, measurements, aggregations, websocket, cache,
    training_data, alerts_history, companies,
    equipment, alert_rules, ml_models, auth_tokens
)
from database import init_db_pool, close_db_pool, create_user_table
from messaging.redis_client import redis_client
from messaging.cache_updater import update_redis_cache

# Authentication imports
from users import auth_backend, fastapi_users, current_active_user
from models.user import User
from schemas import UserCreate, UserRead

import asyncio

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    await redis_client.connect()
    await init_db_pool()

    # Initialize user authentication table
    await create_user_table()
    print("✅ User authentication table initialized")


    cache_task = asyncio.create_task(update_redis_cache())

    yield

    # Shutdown
    cache_task.cancel()
    try:
        await cache_task
    except asyncio.CancelledError:
        pass


    await redis_client.disconnect()
    await close_db_pool()


app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Authentication routes
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)

app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)

app.include_router(
    fastapi_users.get_users_router(UserRead, UserCreate),
    prefix="/users",
    tags=["users"],
)

# Refresh-token auth (login -> access + refresh, /auth/refresh, /auth/logout) — ADR-0004
app.include_router(auth_tokens.router)


# API routers
app.include_router(health.router)
app.include_router(measurements.router)
app.include_router(aggregations.router)
app.include_router(websocket.router)
app.include_router(cache.router)
app.include_router(training_data.router)
app.include_router(alerts_history.router)
app.include_router(alert_rules.router)
app.include_router(ml_models.router)

# Admin endpoints
app.include_router(companies.router)
app.include_router(equipment.router)


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint - API information"""
    return {
        "name": settings.API_TITLE,
        "version": settings.API_VERSION,
        "description": settings.API_DESCRIPTION,
        "docs_url": "/docs",
        "architecture": "Schema-per-tenant (v5.0)",
        "endpoints": {
            "health": "/health",
            "auth_login": "/auth/jwt/login",
            "auth_register": "/auth/register",
            "users_me": "/users/me",
            "measurements": "/api/measurements",
            "measurements_latest": "/api/measurements/latest",
            "aggregations_1min": "/api/aggregations/1min",
            "aggregations_5min": "/api/aggregations/5min",
            "aggregations_1hour": "/api/aggregations/1hour",
            "websocket_sensors": "/ws/sensors",
            "cache_sensors": "/api/cache/sensors",
            "training_data": "/api/training-data/equipment/{equipment_id}",
            "alerts": "/api/alerts",
            "alert_rules": "/api/alert-rules",
            "ml_models": "/api/ml-models",
            "companies": "/api/companies",
            "equipment": "/api/equipment"
        }
    }


# Custom endpoint to list users (for the admin panel).
# Authenticated and tenant-scoped: a caller sees only their own company's users,
# unless they are a superuser (ADR-0004 decisions 4 and 5).
@app.get("/api/users", tags=["users"])
async def list_all_users(current_user: User = Depends(current_active_user)):
    """List users in the caller's company (all companies if superuser)."""
    from database import get_db_pool

    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if current_user.is_superuser:
            rows = await conn.fetch("""
                SELECT id, email, company_id, role, is_active, is_superuser, is_verified
                FROM "user"
                ORDER BY email
            """)
        else:
            rows = await conn.fetch("""
                SELECT id, email, company_id, role, is_active, is_superuser, is_verified
                FROM "user"
                WHERE company_id = $1
                ORDER BY email
            """, current_user.company_id)

        return [dict(row) for row in rows]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
