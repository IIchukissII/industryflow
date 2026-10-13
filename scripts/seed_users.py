#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Seed the first users of a deployment from the environment (inject it with `op run`).

    ADMIN_USER_<n>_EMAIL, ADMIN_USER_<n>_PASSWORD, ADMIN_USER_<n>_COMPANY_ID   (n = 1..9)
    ADMIN_USER_<n>_ROLE        admin | engineer | observer (default admin)
    ADMIN_USER_<n>_SUPERUSER   true to grant cross-tenant superuser (default false)

A user that already exists is skipped. Connects as the API gateway's role with its TLS
verification (ADR-0017). Exits non-zero if nothing is configured or any user could not be
created.

Usage: python scripts/seed_users.py
"""
import asyncio
import os
import sys

import user_admin_db as db
from user_admin_db import UserAdminError

_TRUE = ("1", "true", "yes")


def configured_users(env=os.environ) -> list[dict]:
    users = []
    for i in range(1, 10):
        email = env.get(f"ADMIN_USER_{i}_EMAIL")
        password = env.get(f"ADMIN_USER_{i}_PASSWORD")
        company_id = env.get(f"ADMIN_USER_{i}_COMPANY_ID")
        if not (email or password or company_id):
            continue
        if not (email and password and company_id):
            raise UserAdminError(f"ADMIN_USER_{i}_* is incomplete: EMAIL, PASSWORD and COMPANY_ID are all required")
        users.append({
            "email": email,
            "password": password,
            "company_id": company_id,
            "role": env.get(f"ADMIN_USER_{i}_ROLE") or "admin",
            # Cross-tenant power is opt-in, never implied by the per-tenant admin role (ADR-0004 dec 5).
            "superuser": (env.get(f"ADMIN_USER_{i}_SUPERUSER") or "").strip().lower() in _TRUE,
        })
    return users


async def seed(users: list[dict]) -> int:
    """Create each user in its own transaction. Returns how many failed."""
    failed = 0
    for user in users:
        try:
            db.check_user_fields(user["email"], user["company_id"], user["role"])
            async with db.connect() as conn:
                if await db.user_exists(conn, user["email"]):
                    print(f"⏭️  {user['email']} already exists, skipped")
                    continue
                await db.insert_user(conn, **user)
            print(f"✅ Created {user['email']} (role: {user['role']}, superuser: {user['superuser']})")
        except Exception as e:  # noqa: BLE001 - report each user, then fail the run
            failed += 1
            print(f"❌ {user['email']}: {type(e).__name__}: {e}", file=sys.stderr)
    return failed


def main() -> int:
    try:
        users = configured_users()
    except UserAdminError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    if not users:
        print("❌ No users configured: set ADMIN_USER_1_EMAIL, ADMIN_USER_1_PASSWORD, ADMIN_USER_1_COMPANY_ID",
              file=sys.stderr)
        return 1
    failed = asyncio.run(seed(users))
    if failed:
        print(f"❌ {failed} of {len(users)} user(s) not created", file=sys.stderr)
        return 1
    print(f"✅ Seeding completed ({len(users)} user(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
