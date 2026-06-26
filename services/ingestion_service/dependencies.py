# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication dependencies for Ingestion Service.
"""
from jose import JWTError, jwt
import asyncpg
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from config import get_settings
import uuid

settings = get_settings()
security = HTTPBearer()

# Database connection pool (will be set in startup)
db_pool: Optional[asyncpg.Pool] = None

def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert a company_id UUID to its tenant schema name.

    company_id is validated as a UUID first, so the result is safe to interpolate into a
    SET search_path statement; a non-UUID raises ValueError instead of reaching SQL
    (defends against injection — see ADR-0003).
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"

async def get_current_user_with_company(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Verify JWT token and resolve user's company_id.
    Returns dict with user_id and company_id.
    """
    token = credentials.credentials
    
    try:
        # Decode JWT
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False}
        )
        
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user_id"
            )

        # Prefer the company_id claim from the JWT (ADR-0003 dec 2); avoids the per-request
        # tenant-schema scan below (review finding X2). Fall back to the scan for legacy tokens.
        claim = payload.get("company_id")
        if claim:
            return {"user_id": user_id, "company_id": str(claim)}

        # Resolve company_id from database
        if not db_pool:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database pool not initialized"
            )
        
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
                    return {
                        "user_id": user_id,
                        "company_id": str(company_id)
                    }
        
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in any tenant"
        )
        
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
