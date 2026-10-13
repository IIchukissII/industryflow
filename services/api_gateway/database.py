# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator
from config import get_settings
from db_tls import db_ssl_context
from sqlalchemy import text
from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from models.user import User

settings = get_settings()


# The gateway's ONE connection pool. Each uvicorn worker holds its own, so the role's footprint
# is replicas x workers x (pool_size + max_overflow), which must stay within api_gateway_user's
# CONNECTION LIMIT (00-create-roles.sh). tests/test_db_connection_budget.py checks that sum for
# the compose and Helm defaults.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.API_GATEWAY_DB_POOL_SIZE,
    max_overflow=settings.API_GATEWAY_DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    connect_args={"ssl": db_ssl_context()},
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

async def close_engine():
    """Close the pool's connections on shutdown."""
    await engine.dispose()

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
