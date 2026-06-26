# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication and authorization for Alert Service API
Extracts company_id from database based on user_id in JWT
"""
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from config import Config
import asyncpg

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Validate JWT token and return user info.
    Token must contain user_id (sub).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        token = credentials.credentials
        print(f"🔍 Token received: {token[:50]}... (length: {len(token)}, segments: {len(token.split('.'))})")
        print(f"🔑 JWT_SECRET_KEY length: {len(Config.JWT_SECRET_KEY) if Config.JWT_SECRET_KEY else 'NOT SET'}")
        print(f"🔒 JWT_ALGORITHM: {Config.JWT_ALGORITHM}")

        payload = jwt.decode(
            token,
            Config.JWT_SECRET_KEY,
            algorithms=[Config.JWT_ALGORITHM],
            options={"verify_aud": False}
        )

        user_id: str = payload.get("sub")

        if user_id is None:
            raise credentials_exception

        return {
            "user_id": user_id,
            "payload": payload
        }
    except JWTError as e:
        print(f"❌ JWT Error: {e}")
        raise credentials_exception


async def get_db_pool(request: Request):
    """Get database pool from app state"""
    return request.app.state.db_pool


async def get_company_id(
    current_user: dict = Depends(get_current_user),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
) -> str:
    """
    Resolve company_id, preferring the JWT claim over a tenant-schema scan.
    """
    # Prefer the company_id claim from the JWT (ADR-0003 dec 2); avoids the per-request
    # tenant-schema scan below (review finding X2). Fall back to the scan for legacy tokens.
    claim = current_user.get("payload", {}).get("company_id")
    if claim:
        return str(claim)

    user_id = current_user["user_id"]

    async with db_pool.acquire() as conn:
        # Search all tenant schemas for user
        schemas = await conn.fetch("""
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name LIKE 'tenant_%'
        """)

        for schema_row in schemas:
            schema_name = schema_row['schema_name']
            await conn.execute(f"SET search_path TO {schema_name}, public")

            company_id = await conn.fetchval(
                'SELECT company_id FROM "user" WHERE id = $1',
                user_id
            )

            if company_id:
                return str(company_id)

        raise HTTPException(
            status_code=403,
            detail="User not found in any tenant"
        )
