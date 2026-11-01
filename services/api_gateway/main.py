# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import secrets
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from prometheus_fastapi_instrumentator import Instrumentator

from config import get_settings
from routers import (
    health, measurements, aggregations, websocket, cache,
    training_data, alerts_history, companies,
    equipment, alert_rules, auth_tokens, auth_verify, user_admin, capability_use, platform_switches,
    dashboards_assertion,
)
from database import close_engine, engine
from messaging.redis_client import redis_client
from messaging.cache_updater import update_redis_cache
from messaging import capability_audit_drain

# Authentication imports
from users import auth_backend, fastapi_users, ACCESS_COOKIE

import asyncio

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    await redis_client.connect()

    cache_task = asyncio.create_task(update_redis_cache())
    # The one writer of the capability-use record (ADR-0042 dec 4); the store is the same Redis.
    audit_task = asyncio.create_task(capability_audit_drain.run(redis_client.redis, engine))

    yield

    # Shutdown
    for task in (cache_task, audit_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


    await redis_client.disconnect()
    await close_engine()


app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description=settings.API_DESCRIPTION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    # Explicit origin allowlist (comma-separated in CORS_ORIGINS), never "*" with
    # credentials (ADR-0004 dec 6). Split the string into a list.
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def csrf_protect(request, call_next):
    """
    Double-submit CSRF protection for cookie-authenticated browser requests (ADR-0004
    dec 3). Enforced only on unsafe methods when the request carries the access cookie and
    no bearer header (bearer/API clients are not CSRF-vulnerable). /auth/* is exempt — those
    endpoints authenticate by credentials or the refresh token, not the access cookie.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        cookie_authed = ACCESS_COOKIE in request.cookies and not request.headers.get("authorization")
        if cookie_authed and not request.url.path.startswith("/auth/"):
            csrf_cookie = request.cookies.get(auth_tokens.CSRF_COOKIE)
            csrf_header = request.headers.get("x-csrf-token")
            if not (csrf_cookie and csrf_header and secrets.compare_digest(csrf_cookie, csrf_header)):
                return JSONResponse(status_code=403, content={"detail": "CSRF token missing or invalid"})
    return await call_next(request)


# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# Authentication routes
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)

# No public registration and no generic users router: self-service and tenant-scoped user
# administration live in user_admin (ADR-0003 dec 1, ADR-0004 dec 4-5).
app.include_router(user_admin.router)

# Refresh-token auth (login -> access + refresh, /auth/refresh, /auth/logout) — ADR-0004
app.include_router(auth_tokens.router)
# Session-verification endpoint for the notebook-hub SSO handoff (ADR-0014).
app.include_router(auth_verify.router)
app.include_router(dashboards_assertion.router)


# API routers
app.include_router(health.router)
app.include_router(measurements.router)
app.include_router(aggregations.router)
app.include_router(websocket.router)
app.include_router(cache.router)
app.include_router(training_data.router)
app.include_router(alerts_history.router)
app.include_router(alert_rules.router)

# Admin endpoints
app.include_router(companies.router)
app.include_router(equipment.router)
app.include_router(capability_use.router)
app.include_router(platform_switches.router)


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
            "users_me": "/users/me",
            "users": "/api/users",
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
            "companies": "/api/companies",
            "equipment": "/api/equipment"
        }
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
