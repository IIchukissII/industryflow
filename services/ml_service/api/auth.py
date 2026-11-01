# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication and Authorization
JWT token validation; the tenant is the token's signed company_id claim
"""
import asyncpg
from fastapi import Cookie, Header, HTTPException, Depends, Request
from industryflow_contracts import sessions, tenancy
from jose import jwt, JWTError
from typing import Optional
import logging
import uuid

import config

logger = logging.getLogger(__name__)

# httpOnly access-cookie name — must match the gateway's CookieTransport (ADR-0004 dec 3).
ACCESS_COOKIE = "if_access"

# The gateway signs access tokens with HMAC-SHA256. Pinned, not configurable: a verifier whose
# accepted algorithm is a setting invites algorithm confusion.
JWT_ALGORITHM = "HS256"


# Roles that may change tenant data; observer is read-only (docs/operations/user-management.md).
# The same set the gateway gates its writes with; a superuser passes any role gate.
WRITER_ROLES = ("engineer", "admin")


# The account behind a token, re-read on every request: whether the user exists and is active,
# whether their company (if any) is, and the user's current role. One round trip; the company flag
# rides on the user read.
_ACCOUNT_SQL = """
    SELECT u.is_active AS user_active, u.company_id, c.is_active AS company_active,
           u.role, u.is_superuser
    FROM public."user" u
    LEFT JOIN public.companies c ON c.company_id = u.company_id
    WHERE u.id = $1
"""


async def _usable_account(pool, user_id: str) -> Optional[dict]:
    """The account's role and superuser flag, or None for a missing or inactive user, or a user whose
    company is missing or suspended."""
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_ACCOUNT_SQL, uid)
    if row is None or not row["user_active"]:
        return None
    if row["company_id"] is not None and not row["company_active"]:
        return None
    return {"role": row["role"], "is_superuser": bool(row["is_superuser"])}


async def verify_jwt_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    if_access: Optional[str] = Cookie(None),
) -> dict:
    """
    Verify JWT token and extract user information

    Accepts the access token from the Authorization: Bearer header (API/service
    clients) or, for same-origin browser requests behind the TLS edge, the httpOnly
    if_access cookie (ADR-0004 dec 3).

    A valid signature is not enough: every request re-reads the account, and a deactivated or
    deleted user, or a user of a suspended company, is refused at once rather than when the
    token expires (ADR-0004 dec 2, ADR-0033 dec 3). So is a token whose session has ended — logout, a
    password change or reset — read from the gateway's ended-session record
    (``industryflow_contracts.sessions``).

    The role is read from the account on the same round trip, not from the token or the request:
    a role change applies to the next request, like a deactivation.

    Returns:
        dict with user_id, the token's claims (payload), and the account's role and is_superuser

    Raises:
        HTTPException: 401 if token invalid or missing, or the account is not usable;
        503 if the account or the session store cannot be read
    """
    if authorization and authorization.startswith('Bearer '):
        token = authorization.replace('Bearer ', '')
    elif if_access:
        token = if_access
    else:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid authorization header"
        )

    try:
        payload = jwt.decode(
            token,
            config.config.JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_aud": False}
        )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError as e:
        logger.error(f"JWT validation error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Account store not available")
    try:
        account = await _usable_account(pool, user_id)
    except (asyncpg.PostgresError, OSError) as e:
        logger.error(f"Account check failed: {e}")
        raise HTTPException(status_code=503, detail="Account store not available")
    if account is None:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Session store not available")
    try:
        ended = await sessions.session_ended(store, user_id, payload.get("sid"), payload.get("iat"))
    except Exception as e:  # noqa: BLE001 - any store failure leaves the session unknown
        logger.error(f"Session check failed: {type(e).__name__}")
        raise HTTPException(status_code=503, detail="Session store not available")
    if ended:
        raise HTTPException(status_code=401, detail="Session ended")
    return {"user_id": user_id, "payload": payload, **account}


async def require_writer(current_user: dict = Depends(verify_jwt_token)) -> dict:
    """Dependency for every route that changes tenant data: the caller's account must hold a writer
    role, or be a superuser. An observer is refused with 403.

    The role is the one ``verify_jwt_token`` read from the account; nothing the client sends, the
    token's own ``role`` claim included, is consulted.
    """
    if current_user.get("is_superuser") is True or current_user.get("role") in WRITER_ROLES:
        return current_user
    raise HTTPException(
        status_code=403,
        detail=f"Required role: {' or '.join(WRITER_ROLES)}; an observer has read-only access",
    )


def company_id_of(current_user: dict) -> str:
    """The caller's tenant: the ``company_id`` claim of their verified token, canonicalized.

    The gateway signs the tenant into every access token it issues (ADR-0003 dec 2, ADR-0004 dec 2),
    so the tenant is read from the credential — never discovered by scanning tenant schemas for the
    user (review finding X2), and never taken from the request. A token without the claim, or with
    one that is not a UUID, names no tenant and is refused.
    """
    claim = current_user.get("payload", {}).get("company_id")
    if not claim:
        raise HTTPException(status_code=401, detail="Token carries no tenant")
    try:
        return tenancy.canonical_company_id(claim)
    except ValueError:
        raise HTTPException(status_code=401, detail="Token carries an invalid tenant")


async def get_company_id(current_user: dict = Depends(verify_jwt_token)) -> str:
    """Dependency: the verified caller's tenant (see ``company_id_of``)."""
    return company_id_of(current_user)
