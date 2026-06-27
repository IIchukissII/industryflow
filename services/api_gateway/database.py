# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator, Optional
from config import get_settings
from sqlalchemy import text
import asyncpg
from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from models.user import User

settings = get_settings()

# Create async engine with optimized connection pool
# 8 workers × 18 connections per worker = 144 connections (fits in 150 allocated)
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=18,
    max_overflow=2,
    pool_pre_ping=True,
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency function that provides database session.
    Usage in FastAPI: db: AsyncSession = Depends(get_db)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def test_connection():
    """Test database connection"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        return result.scalar() == 1

# ============================================================================
# AsyncPG Connection Pool
# ============================================================================
_db_pool: Optional[asyncpg.Pool] = None

async def get_db_pool() -> asyncpg.Pool:
    """
    Get or create asyncpg database connection pool.
    """
    global _db_pool
    if _db_pool is None:
        _db_pool = await asyncpg.create_pool(
            host=settings.timescaledb_host,
            port=settings.timescaledb_port,
            user=settings.timescaledb_user,
            password=settings.timescaledb_password,
            database=settings.timescaledb_database,
            min_size=5,
            max_size=20,
            command_timeout=60
        )
    return _db_pool

async def close_db_pool():
    """Close asyncpg database pool on shutdown"""
    global _db_pool
    if _db_pool:
        await _db_pool.close()
        _db_pool = None

async def init_db_pool():
    """Initialize asyncpg database pool on startup"""
    await get_db_pool()

async def get_user_db(session: AsyncSession = Depends(get_db)):
    """
    Dependency for getting user database adapter for fastapi-users.
    """
    yield SQLAlchemyUserDatabase(session, User)

async def create_user_table():
    """
    Create the users table in the database.
    Call this once during initialization.
    """
    from models.user import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
