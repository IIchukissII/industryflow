#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
User management CLI for bootstrapping and recovery (docs/operations/user-management.md).

Connects as the API gateway's role with its TLS verification (verify-full by default, ADR-0017).
Database credentials come from the environment (inject them with `op run`). A password is never
an argument: it is prompted for, or read from stdin with --password-stdin. Exits non-zero on any
failure.

Usage:
    python scripts/manage_users.py create --email user@example.com --company-id UUID --role admin [--superuser]
    python scripts/manage_users.py list
    python scripts/manage_users.py delete --email user@example.com [--yes]
    python scripts/manage_users.py reset-password --email user@example.com [--password-stdin]
"""
import argparse
import asyncio
import sys
from getpass import getpass

from sqlalchemy import text

import user_admin_db as db
from user_admin_db import UserAdminError


def read_password(args, prompt: str) -> str:
    """The new password: one line from stdin with --password-stdin, else prompted for twice."""
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass(f"{prompt}: ")
        if password != getpass(f"Confirm {prompt.lower()}: "):
            raise UserAdminError("passwords don't match")
    if not password:
        raise UserAdminError("empty password")
    return password


async def create_user(args) -> None:
    db.check_user_fields(args.email, args.company_id, args.role)
    password = read_password(args, "Password")
    async with db.connect() as conn:
        user_id = await db.insert_user(
            conn, email=args.email, password=password, company_id=args.company_id,
            role=args.role, superuser=args.superuser)
    print("=" * 80)
    print("✅ User created")
    print(f"Email:      {args.email}")
    print(f"User ID:    {user_id}")
    print(f"Company ID: {args.company_id}")
    print(f"Role:       {args.role}")
    print(f"Superuser:  {'Yes' if args.superuser else 'No'}")
    print("=" * 80)


async def list_users(args) -> None:
    async with db.connect() as conn:
        result = await conn.execute(text("""
            SELECT u.email, c.company_name, u.role, u.is_active, u.is_superuser, u.is_verified
            FROM public."user" u
            LEFT JOIN public.companies c ON c.company_id = u.company_id
            ORDER BY u.email
        """))
        users = result.all()

    print("=" * 120)
    print(f"{'EMAIL':<30} {'COMPANY':<25} {'ROLE':<12} {'ACTIVE':<8} {'SUPERUSER':<12} {'VERIFIED':<10}")
    print("=" * 120)
    for email, company_name, role, is_active, is_superuser, is_verified in users:
        print(
            f"{email:<30} {company_name or 'N/A':<25} {role:<12} "
            f"{'Yes' if is_active else 'No':<8} {'Yes' if is_superuser else 'No':<12} "
            f"{'Yes' if is_verified else 'No':<10}"
        )
    print("=" * 120)
    print(f"Total users: {len(users)}")


async def delete_user(args) -> None:
    if not args.yes:
        confirm = input(f"Are you sure you want to delete user {args.email}? (yes/no): ")
        if confirm.lower() != "yes":
            raise UserAdminError("deletion cancelled")
    async with db.connect() as conn:
        result = await conn.execute(
            text('DELETE FROM public."user" WHERE email = :email RETURNING id'), {"email": args.email})
        if result.first() is None:
            raise UserAdminError(f"user {args.email} not found")
    print(f"✅ User {args.email} deleted")


async def reset_password(args) -> None:
    password = read_password(args, "New password")
    async with db.connect() as conn:
        result = await conn.execute(
            text('UPDATE public."user" SET hashed_password = :hashed WHERE email = :email RETURNING id'),
            {"hashed": db.hash_password(password), "email": args.email},
        )
        if result.first() is None:
            raise UserAdminError(f"user {args.email} not found")
    # The user's refresh tokens carry a fingerprint of the old hash, so existing sessions end at
    # their next refresh (services/api_gateway/messaging/refresh_tokens.py).
    print(f"✅ Password reset for {args.email}; existing sessions end at their next refresh")


COMMANDS = {
    "create": create_user,
    "list": list_users,
    "delete": delete_user,
    "reset-password": reset_password,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="User Management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a user in an existing company")
    create.add_argument("--email", required=True)
    create.add_argument("--company-id", required=True, help="Company UUID")
    create.add_argument("--role", required=True, choices=db.VALID_ROLES)
    create.add_argument("--superuser", action="store_true",
                        help="Grant cross-tenant superuser (never implied by --role admin)")
    create.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")

    subparsers.add_parser("list", help="List all users")

    delete = subparsers.add_parser("delete", help="Delete a user")
    delete.add_argument("--email", required=True)
    delete.add_argument("--yes", "-y", action="store_true", help="Skip confirmation")

    reset = subparsers.add_parser("reset-password", help="Set a user's password")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(COMMANDS[args.command](args))
    except UserAdminError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - any DB/connection failure is a failed run
        print(f"❌ {args.command} failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
