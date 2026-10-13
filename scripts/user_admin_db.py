# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database access shared by scripts/manage_users.py and scripts/seed_users.py.

Both connect as the API gateway's role, with the gateway's own TLS verification (ADR-0017:
verify-full against DB_SSLROOTCERT unless DB_SSLMODE says otherwise) and its own password
hashing, and write users only with bound parameters.
"""
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# The gateway's modules: db_tls (the TLS context) and nothing that needs the app's settings.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api_gateway"))

from db_tls import db_ssl_context  # noqa: E402
from fastapi_users.password import PasswordHelper  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

VALID_ROLES = ("admin", "engineer", "observer")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserAdminError(Exception):
    """A refusal or failure to report to the operator (the script then exits non-zero)."""


def database_url() -> URL:
    """The gateway role's connection URL, from the same environment the gateway reads."""
    password = os.getenv("API_GATEWAY_DB_PASSWORD")
    if not password:
        raise UserAdminError("API_GATEWAY_DB_PASSWORD is not set")
    return URL.create(
        "postgresql+asyncpg",
        username=os.getenv("API_GATEWAY_DB_USER", "api_gateway_user"),
        password=password,
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "industryflow"),
    )


@asynccontextmanager
async def connect():
    """One transaction on one connection (no pool: these are one-shot tools)."""
    engine = create_async_engine(database_url(), poolclass=NullPool, connect_args={"ssl": db_ssl_context()})
    try:
        async with engine.begin() as conn:
            yield conn
    finally:
        await engine.dispose()


def hash_password(password: str) -> str:
    """Hash exactly as the gateway's UserManager does (fastapi-users' PasswordHelper)."""
    return PasswordHelper().hash(password)


def check_user_fields(email: str, company_id: str, role: str) -> str:
    """Validate the fields of a new user; returns the canonical company_id."""
    if not _EMAIL.match(email or ""):
        raise UserAdminError(f"not an email address: {email!r}")
    if role not in VALID_ROLES:
        raise UserAdminError(f"role must be one of {', '.join(VALID_ROLES)}")
    try:
        return str(uuid.UUID(str(company_id)))
    except ValueError:
        raise UserAdminError(f"company_id is not a UUID: {company_id!r}")


async def user_exists(conn, email: str) -> bool:
    result = await conn.execute(text('SELECT 1 FROM public."user" WHERE email = :email'), {"email": email})
    return result.first() is not None


async def insert_user(conn, *, email: str, password: str, company_id: str, role: str, superuser: bool) -> str:
    """Insert a user into an existing company. Superuser only when explicitly asked for."""
    company_id = check_user_fields(email, company_id, role)
    if not password:
        raise UserAdminError("empty password")
    found = await conn.execute(
        text("SELECT 1 FROM public.companies WHERE company_id = :company_id"), {"company_id": uuid.UUID(company_id)})
    if found.first() is None:
        raise UserAdminError(f"no company {company_id}")
    if await user_exists(conn, email):
        raise UserAdminError(f"user {email} already exists")
    user_id = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO public."user" (
                id, email, hashed_password, company_id, role, is_active, is_superuser, is_verified
            ) VALUES (
                :id, :email, :hashed_password, :company_id, :role, true, :is_superuser, true
            )
        """),
        {
            "id": user_id,
            "email": email,
            "hashed_password": hash_password(password),
            "company_id": uuid.UUID(company_id),
            "role": role,
            "is_superuser": bool(superuser),
        },
    )
    return str(user_id)
