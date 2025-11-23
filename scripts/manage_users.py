#!/usr/bin/env python3
"""
Production-ready user management CLI tool.
Provides commands to create, list, update, and delete users.

Usage:
    python scripts/manage_users.py create --email user@example.com --password SecurePass123! --company-id UUID --role admin
    python scripts/manage_users.py list
    python scripts/manage_users.py delete --email user@example.com
    python scripts/manage_users.py reset-password --email user@example.com --password NewPassword123!
"""
import asyncio
import argparse
import os
import sys
from pathlib import Path
from getpass import getpass

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "api_gateway"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext
import uuid

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_database_url():
    """Get database URL from environment."""
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "industryflow")
    db_user = os.getenv("API_GATEWAY_DB_USER", "api_gateway")
    db_password = os.getenv("API_GATEWAY_DB_PASSWORD")

    if not db_password:
        print("❌ Error: API_GATEWAY_DB_PASSWORD environment variable not set")
        sys.exit(1)

    return f"postgresql+asyncpg://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


async def create_user(args):
    """Create a new user."""
    engine = create_async_engine(get_database_url(), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Get password securely if not provided
    password = args.password
    if not password:
        password = getpass("Enter password: ")
        password_confirm = getpass("Confirm password: ")
        if password != password_confirm:
            print("❌ Passwords don't match")
            return

    async with async_session() as session:
        try:
            # Check if user exists
            result = await session.execute(
                f"""SELECT id FROM "user" WHERE email = '{args.email}'"""
            )
            if result.fetchone():
                print(f"❌ User {args.email} already exists")
                return

            # Validate role
            valid_roles = ['admin', 'engineer', 'observer']
            if args.role not in valid_roles:
                print(f"❌ Invalid role. Must be one of: {', '.join(valid_roles)}")
                return

            # Hash password
            hashed_password = pwd_context.hash(password)
            user_id = str(uuid.uuid4())

            # Insert user
            await session.execute(
                f"""
                INSERT INTO "user" (
                    id, email, hashed_password, company_id, role,
                    is_active, is_superuser, is_verified
                ) VALUES (
                    '{user_id}',
                    '{args.email}',
                    '{hashed_password}',
                    '{args.company_id}',
                    '{args.role}',
                    true,
                    {args.role == 'admin'},
                    true
                )
                """
            )
            await session.commit()

            print("=" * 80)
            print("✅ User created successfully")
            print("=" * 80)
            print(f"Email:      {args.email}")
            print(f"User ID:    {user_id}")
            print(f"Company ID: {args.company_id}")
            print(f"Role:       {args.role}")
            print(f"Active:     Yes")
            print("=" * 80)

        except Exception as e:
            print(f"❌ Error creating user: {e}")
            await session.rollback()

    await engine.dispose()


async def list_users(args):
    """List all users."""
    engine = create_async_engine(get_database_url(), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.execute(
            """
            SELECT
                u.id, u.email, u.company_id, u.role, u.is_active,
                u.is_superuser, u.is_verified, c.name as company_name
            FROM "user" u
            LEFT JOIN company c ON u.company_id = c.id
            ORDER BY u.email
            """
        )
        users = result.fetchall()

        print("=" * 120)
        print(f"{'EMAIL':<30} {'COMPANY':<25} {'ROLE':<12} {'ACTIVE':<8} {'SUPERUSER':<12} {'VERIFIED':<10}")
        print("=" * 120)

        for user in users:
            print(
                f"{user[1]:<30} {user[7] or 'N/A':<25} {user[3]:<12} "
                f"{'Yes' if user[4] else 'No':<8} {'Yes' if user[5] else 'No':<12} "
                f"{'Yes' if user[6] else 'No':<10}"
            )

        print("=" * 120)
        print(f"Total users: {len(users)}")
        print("=" * 120)

    await engine.dispose()


async def delete_user(args):
    """Delete a user."""
    engine = create_async_engine(get_database_url(), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Confirm deletion
    if not args.yes:
        confirm = input(f"Are you sure you want to delete user {args.email}? (yes/no): ")
        if confirm.lower() != 'yes':
            print("❌ Deletion cancelled")
            return

    async with async_session() as session:
        try:
            result = await session.execute(
                f"""DELETE FROM "user" WHERE email = '{args.email}' RETURNING id"""
            )
            deleted_user = result.fetchone()

            if deleted_user:
                await session.commit()
                print(f"✅ User {args.email} deleted successfully")
            else:
                print(f"❌ User {args.email} not found")

        except Exception as e:
            print(f"❌ Error deleting user: {e}")
            await session.rollback()

    await engine.dispose()


async def reset_password(args):
    """Reset user password."""
    engine = create_async_engine(get_database_url(), echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Get password securely if not provided
    password = args.password
    if not password:
        password = getpass("Enter new password: ")
        password_confirm = getpass("Confirm new password: ")
        if password != password_confirm:
            print("❌ Passwords don't match")
            return

    async with async_session() as session:
        try:
            # Hash password
            hashed_password = pwd_context.hash(password)

            # Update password
            result = await session.execute(
                f"""
                UPDATE "user"
                SET hashed_password = '{hashed_password}'
                WHERE email = '{args.email}'
                RETURNING id
                """
            )
            updated_user = result.fetchone()

            if updated_user:
                await session.commit()
                print(f"✅ Password reset successfully for {args.email}")
            else:
                print(f"❌ User {args.email} not found")

        except Exception as e:
            print(f"❌ Error resetting password: {e}")
            await session.rollback()

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="User Management CLI")
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Create user command
    create_parser = subparsers.add_parser('create', help='Create a new user')
    create_parser.add_argument('--email', required=True, help='User email address')
    create_parser.add_argument('--password', help='User password (will prompt if not provided)')
    create_parser.add_argument('--company-id', required=True, help='Company UUID')
    create_parser.add_argument('--role', required=True, choices=['admin', 'engineer', 'observer'], help='User role')

    # List users command
    list_parser = subparsers.add_parser('list', help='List all users')

    # Delete user command
    delete_parser = subparsers.add_parser('delete', help='Delete a user')
    delete_parser.add_argument('--email', required=True, help='User email address')
    delete_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')

    # Reset password command
    reset_parser = subparsers.add_parser('reset-password', help='Reset user password')
    reset_parser.add_argument('--email', required=True, help='User email address')
    reset_parser.add_argument('--password', help='New password (will prompt if not provided)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()

    # Run command
    if args.command == 'create':
        asyncio.run(create_user(args))
    elif args.command == 'list':
        asyncio.run(list_users(args))
    elif args.command == 'delete':
        asyncio.run(delete_user(args))
    elif args.command == 'reset-password':
        asyncio.run(reset_password(args))


if __name__ == "__main__":
    main()
