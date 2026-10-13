# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication and Authorization
JWT token validation; the tenant is the token's signed company_id claim
"""
import asyncpg
from fastapi import Cookie, Header, HTTPException, Depends, Request
from industryflow_contracts import tenancy
from jose import jwt, JWTError
from typing import Optional
import logging
import uuid

import config

logger = logging.getLogger(__name__)

# httpOnly access-cookie name — must match the gateway's CookieTransport (ADR-0004 dec 3).
ACCESS_COOKIE = "if_access"


# The account behind a token, re-read on every request: whether the user exists and is active,
# and whether their company (if any) is. One round trip; the company flag rides on the user read.
_ACCOUNT_SQL = """
    SELECT u.is_active AS user_active, u.company_id, c.is_active AS company_active
    FROM public."user" u
    LEFT JOIN public.companies c ON c.company_id = u.company_id
    WHERE u.id = $1
"""


async def _account_is_usable(pool, user_id: str) -> bool:
    """False for a missing or inactive user, or a user whose company is missing or suspended."""
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        return False
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_ACCOUNT_SQL, uid)
    if row is None or not row["user_active"]:
        return False
    return row["company_id"] is None or bool(row["company_active"])


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
    token expires (ADR-0004 dec 2 rev 2, ADR-0033 dec 3).

    Returns:
        dict with user_id and other claims

    Raises:
        HTTPException: 401 if token invalid or missing, or the account is not usable;
        503 if the account cannot be read
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
            algorithms=[config.config.JWT_ALGORITHM],
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
        usable = await _account_is_usable(pool, user_id)
    except (asyncpg.PostgresError, OSError) as e:
        logger.error(f"Account check failed: {e}")
        raise HTTPException(status_code=503, detail="Account store not available")
    if not usable:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return {"user_id": user_id, "payload": payload}


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
