# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from dataclasses import dataclass
from typing import AsyncGenerator, Optional
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from models.user import User
from users import current_active_user, fastapi_users
from database import AsyncSessionLocal
from capability_auth import resolve_capability, AUDIENCE_API
import logging
import uuid

logger = logging.getLogger(__name__)

# Optional session: yields the user if a valid session is present, else None (no 401), so the
# data path can fall back to a notebook capability (ADR-0011 dec 4, ADR-0015).
optional_active_user = fastapi_users.current_user(active=True, optional=True)

# Header the notebook client sends the API-audience capability handle in (ADR-0015).
CAPABILITY_HEADER = "X-IF-Capability"

async def get_current_user_with_company(
    current_user: User = Depends(current_active_user)
) -> User:
    """
    Dependency that returns current authenticated user.
    Ensures user has a valid company_id.
    """
    if not current_user.company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no company association"
        )
    return current_user

def require_role(required_role: str):
    """
    Dependency factory for role-based access control.
    Usage: user = Depends(require_role("admin"))
    """
    async def role_checker(current_user: User = Depends(current_active_user)) -> User:
        # Superusers pass any role gate; otherwise the role must match exactly.
        if current_user.is_superuser:
            return current_user
        if current_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {required_role}"
            )
        return current_user
    return role_checker

def require_any_role(*roles: str):
    """
    Dependency factory for multiple allowed roles.
    Usage: user = Depends(require_any_role("admin", "engineer"))
    """
    async def role_checker(current_user: User = Depends(current_active_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required one of roles: {', '.join(roles)}"
            )
        return current_user
    return role_checker

# ============================================================================
# Schema-Per-Tenant Database Dependencies
# ============================================================================

def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert a company_id UUID to its tenant schema name.

    company_id is validated as a UUID first, so the result is safe to interpolate into a
    SET search_path statement; a non-UUID raises ValueError instead of reaching SQL
    (defends against injection — see ADR-0003).
    Example: '550e8400-e29b-41d4-a716-446655440000' -> 'tenant_550e8400_e29b_41d4_a716_446655440000'
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"

async def get_db_with_tenant(
    current_user: User = Depends(current_active_user)
) -> AsyncGenerator[AsyncSession, None]:
    """
    Database session dependency with schema-per-tenant routing.
    
    Sets the PostgreSQL search_path to the user's tenant schema,
    enabling automatic query routing to the correct tenant.
    
    Usage:
        @router.get("/my-sensors")
        async def get_sensors(db: AsyncSession = Depends(get_db_with_tenant)):
            # Query will automatically route to tenant schema
            result = await db.execute(select(Sensor))
    """
    async with AsyncSessionLocal() as session:
        try:
            # Scope the tenant search_path to a transaction via SET LOCAL, so it is
            # discarded when the transaction ends and never lingers on the pooled
            # connection for the next borrower (ADR-0003 decision 6).
            async with session.begin():
                if current_user and current_user.company_id:
                    schema_name = normalize_company_id_to_schema(current_user.company_id)
                    await session.execute(
                        text(f"SET LOCAL search_path TO {schema_name}, public")
                    )
                    logger.debug(f"Schema search path set to: {schema_name}")
                else:
                    logger.warning("User has no company_id, schema routing not set")

                yield session
            # session.begin() commits on exit (or rolls back on error); the SET LOCAL
            # search_path is discarded with the transaction.
        except Exception as e:
            logger.error(f"Database error with tenant routing: {e}")
            raise
        finally:
            await session.close()


# ============================================================================
# Unified tenant access: platform session OR notebook capability (ADR-0015)
# ============================================================================

@dataclass(frozen=True)
class TenantContext:
    """The resolved tenant for a data request, from a session or a capability."""

    company_id: str
    user: Optional[str]
    read_only: bool
    source: str  # "session" | "capability"


async def _capability_get(key: str):
    """Async getter over the shared store for capability resolution (isolated for tests)."""
    from messaging.redis_client import redis_client

    return await redis_client.redis.get(key)


async def resolve_tenant_context(
    request: Request,
    user: Optional[User] = Depends(optional_active_user),
) -> TenantContext:
    """Resolve the caller's tenant from the platform session, else a notebook capability.

    A logged-in user resolves to their tenant (full access, as today). Otherwise the request
    must carry a valid API-audience capability (ADR-0015), which resolves to a single tenant
    with read-only intent. Neither present → 401.
    """
    if user is not None and getattr(user, "company_id", None):
        return TenantContext(company_id=str(user.company_id), user=str(user.id), read_only=False, source="session")

    handle = request.headers.get(CAPABILITY_HEADER, "")
    binding = await resolve_capability(_capability_get, handle, expected_audience=AUDIENCE_API)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No valid session or capability",
        )
    return TenantContext(company_id=binding.company_id, user=binding.user, read_only=True, source="capability")


async def get_tenant_db(
    ctx: TenantContext = Depends(resolve_tenant_context),
) -> AsyncGenerator[AsyncSession, None]:
    """Tenant-scoped DB session for the data API, accepting a session or a capability.

    Sets the tenant search_path transaction-locally (ADR-0003 dec 6); a capability-sourced
    request additionally runs read-only, so a notebook can never write through the data API
    even though the gateway's own role can (defense in depth alongside the read-only tenant
    role of ADR-0011).
    """
    async with AsyncSessionLocal() as session:
        try:
            async with session.begin():
                schema_name = normalize_company_id_to_schema(ctx.company_id)
                await session.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
                if ctx.read_only:
                    await session.execute(text("SET LOCAL transaction read only"))
                yield session
        except Exception as e:
            logger.error(f"Database error with tenant routing: {e}")
            raise
        finally:
            await session.close()
