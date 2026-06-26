#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Production-ready user seeding script.
Creates initial admin users from environment variables.
Usage: python scripts/seed_users.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path to import from services
sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "api_gateway"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext
import uuid

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def seed_users():
    """Seed initial admin users from environment variables."""

    # Database connection
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "industryflow")
    db_user = os.getenv("API_GATEWAY_DB_USER", "api_gateway")
    db_password = os.getenv("API_GATEWAY_DB_PASSWORD")

    if not db_password:
        print("❌ Error: API_GATEWAY_DB_PASSWORD environment variable not set")
        return

    database_url = f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    # Create engine
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("=" * 80)
    print("USER SEEDING - Production Ready")
    print("=" * 80)

    # Get admin users from environment
    admin_users = []

    # Support multiple admin users via numbered env vars
    for i in range(1, 10):  # Support up to 9 admin users
        email = os.getenv(f"ADMIN_USER_{i}_EMAIL")
        password = os.getenv(f"ADMIN_USER_{i}_PASSWORD")
        company_id = os.getenv(f"ADMIN_USER_{i}_COMPANY_ID")
        role = os.getenv(f"ADMIN_USER_{i}_ROLE", "admin")

        if email and password and company_id:
            admin_users.append({
                "email": email,
                "password": password,
                "company_id": company_id,
                "role": role
            })

    if not admin_users:
        print("⚠️  No admin users configured in environment variables")
        print("   Set ADMIN_USER_1_EMAIL, ADMIN_USER_1_PASSWORD, ADMIN_USER_1_COMPANY_ID")
        return

    async with async_session() as session:
        for user_data in admin_users:
            try:
                # Check if user already exists
                result = await session.execute(
                    f"""SELECT id FROM "user" WHERE email = '{user_data['email']}'"""
                )
                existing_user = result.fetchone()

                if existing_user:
                    print(f"⏭️  User {user_data['email']} already exists, skipping...")
                    continue

                # Hash password
                hashed_password = pwd_context.hash(user_data['password'])
                user_id = str(uuid.uuid4())

                # Insert user
                await session.execute(
                    f"""
                    INSERT INTO "user" (
                        id, email, hashed_password, company_id, role,
                        is_active, is_superuser, is_verified
                    ) VALUES (
                        '{user_id}',
                        '{user_data['email']}',
                        '{hashed_password}',
                        '{user_data['company_id']}',
                        '{user_data['role']}',
                        true,
                        {user_data['role'] == 'admin'},
                        true
                    )
                    """
                )
                await session.commit()

                print(f"✅ Created user: {user_data['email']} (role: {user_data['role']})")

            except Exception as e:
                print(f"❌ Error creating user {user_data['email']}: {e}")
                await session.rollback()

    await engine.dispose()
    print("=" * 80)
    print("✅ User seeding completed")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(seed_users())
