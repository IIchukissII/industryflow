# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication and Authorization
JWT token validation with database company_id lookup
"""
import asyncpg
from fastapi import Header, HTTPException, Depends
from jose import jwt, JWTError
from typing import Optional
import logging

import config

logger = logging.getLogger(__name__)


async def verify_jwt_token(authorization: Optional[str] = Header(None)) -> dict:
    """
    Verify JWT token and extract user information
    
    Returns:
        dict with user_id and other claims
    
    Raises:
        HTTPException: 401 if token invalid or missing
    """
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid authorization header"
        )
    
    token = authorization.replace('Bearer ', '')
    
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
        
        return {"user_id": user_id, "payload": payload}
        
    except JWTError as e:
        logger.error(f"JWT validation error: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def get_company_id(
    current_user: dict = Depends(verify_jwt_token),
    db_pool: asyncpg.Pool = None
) -> str:
    """
    Resolve company_id from user_id by searching all tenant schemas
    
    Args:
        current_user: User information from JWT token
        db_pool: Database connection pool (injected as dependency)
    
    Returns:
        company_id as string (UUID)
    
    Raises:
        HTTPException: 404 if user not found in any tenant schema
    """
    user_id = current_user["user_id"]
    
    # Get pool from app state if not provided
    if db_pool is None:
        raise HTTPException(status_code=500, detail="Database pool not available")
    
    async with db_pool.acquire() as conn:
        # Get all tenant schemas
        schemas = await conn.fetch("""
            SELECT schema_name 
            FROM information_schema.schemata 
            WHERE schema_name LIKE 'tenant_%'
            ORDER BY schema_name
        """)
        
        # Search each tenant schema for the user
        for schema_row in schemas:
            schema_name = schema_row['schema_name']
            
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            company_id = await conn.fetchval(
                'SELECT company_id FROM "user" WHERE id = $1',
                user_id
            )
            
            if company_id:
                logger.debug(f"User {user_id} found in schema {schema_name}")
                return str(company_id)
        
        # User not found in any schema
        logger.warning(f"User {user_id} not found in any tenant schema")
        raise HTTPException(
            status_code=404,
            detail="User not found or not associated with any company"
        )
