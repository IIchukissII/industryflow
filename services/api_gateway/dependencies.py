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
from capability_auth import AUDIENCE_API, record_resolution, resolve_capability_outcome
import company_status
from industryflow_contracts import tenancy
import logging

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

def require_role(*allowed_roles: str):
    """
    Dependency factory for role-based access control: the caller's role must be one of
    ``allowed_roles``. Usage: user = Depends(require_role("admin"))
    """
    async def role_checker(current_user: User = Depends(current_active_user)) -> User:
        # Superusers pass any role gate; otherwise the role must be one of those allowed.
        if current_user.is_superuser:
            return current_user
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role: {' or '.join(allowed_roles)}"
            )
        return current_user
    return role_checker


# Roles that may change tenant data (equipment, sensors, alert rules, alert acknowledgement and
# labels). observer is read-only (docs/operations/user-management.md).
WRITER_ROLES = ("engineer", "admin")


async def get_writer_with_company(
    current_user: User = Depends(require_role(*WRITER_ROLES)),
) -> User:
    """A caller allowed to write tenant data, with a company to route the write to."""
    return await get_current_user_with_company(current_user)

# ============================================================================
# Schema-Per-Tenant Database Dependencies
# ============================================================================

# The one company_id -> schema mapping (ADR-0003 dec 5, 7), under the name this service imports it
# by. The company_id is validated as a UUID first, so the result is safe to emit in a SET LOCAL
# search_path statement; a non-UUID raises ValueError instead of reaching SQL.
normalize_company_id_to_schema = tenancy.schema_name

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
                        text(f'SET LOCAL search_path TO "{schema_name}", public')
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


def _capability_store():
    """The shared store the capability-use record is appended to (isolated for tests)."""
    from messaging.redis_client import redis_client

    return redis_client.redis


async def resolve_tenant_context(
    request: Request,
    user: Optional[User] = Depends(optional_active_user),
) -> TenantContext:
    """Resolve the caller's tenant from the platform session, else a notebook capability.

    A logged-in user resolves to their tenant (full access, as today). Otherwise the request
    must carry a valid API-audience capability (ADR-0015), which resolves to a single tenant
    with read-only intent. Neither present, or the tenant suspended → 401.
    """
    if user is not None and getattr(user, "company_id", None):
        return TenantContext(company_id=str(user.company_id), user=str(user.id), read_only=False, source="session")

    handle = request.headers.get(CAPABILITY_HEADER, "")
    binding, reason, facts = await resolve_capability_outcome(
        _capability_get, handle, expected_audience=AUDIENCE_API)
    # A suspended company's handles are refused here as its sessions are (ADR-0033 dec 3): by the
    # store's suspension marker inside the resolution, and by the registry's flag here.
    if binding is not None and not await company_status.is_company_active(binding.company_id):
        binding, reason = None, "tenant_inactive"
    # A presented handle's outcome is recorded (ADR-0042 dec 1); a request with neither a session
    # nor a handle presented no capability.
    if handle:
        await record_resolution(_capability_store(), handle, binding, reason, facts,
                                source=request.client.host if getattr(request, "client", None) else None)
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
                await session.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
                if ctx.read_only:
                    await session.execute(text("SET LOCAL transaction read only"))
                yield session
        except Exception as e:
            logger.error(f"Database error with tenant routing: {e}")
            raise
        finally:
            await session.close()
