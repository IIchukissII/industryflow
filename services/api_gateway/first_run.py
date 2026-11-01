# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The first-run superuser (ADR-0004 dec 11): a platform with no account at all gets one at start.

Only while the platform has no account of any kind, the gateway creates a superuser,
``PLATFORM_ADMIN_EMAIL`` with ``PLATFORM_ADMIN_PASSWORD``, in the oldest active tenant, or in a first
tenant it provisions (``PLATFORM_ADMIN_TENANT``) when there is none. With the published default password the account must
change it before anything else (``password_change_required``); a password the deployment set itself
is not forced. Once any account exists this does nothing: a populated platform never gains a superuser
with a published password, whatever became of its superusers (the CLI makes one there).

Replicas starting together, and the CLI inserting a user, are serialised by a transaction-scoped
advisory lock (``LOCK_KEY``).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from fastapi_users.password import PasswordHelper
from sqlalchemy import text

logger = logging.getLogger(__name__)

DEFAULT_EMAIL = "admin@industryflow.local"
DEFAULT_PASSWORD = "industryflow"
DEFAULT_TENANT = "Default"
# Any constant shared by every gateway replica; it names this one critical section.
LOCK_KEY = 0x1F_5E_57_AD


async def ensure_first_superuser(session, email: Optional[str], password: Optional[str],
                                 tenant: Optional[str]) -> Optional[dict]:
    """Create the first-run superuser if the platform has no account; returns what was created, else None.

    ``session`` is an AsyncSession as the gateway's role; the caller commits nothing, this does."""
    email = (email or DEFAULT_EMAIL).strip().lower()
    password = password or DEFAULT_PASSWORD
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
    exists = await session.execute(text('SELECT 1 FROM public."user" LIMIT 1'))
    if exists.first() is not None:
        await session.rollback()
        return None
    row = (await session.execute(text(
        "SELECT company_id FROM public.companies WHERE is_active ORDER BY created_at, company_id LIMIT 1"))).first()
    provisioned = row is None
    if provisioned:
        row = (await session.execute(text("SELECT * FROM public.provision_company(:name, true)"),
                                     {"name": tenant or DEFAULT_TENANT})).first()
    company_id = row.company_id
    user_id = uuid.uuid4()
    forced = password == DEFAULT_PASSWORD
    await session.execute(text(
        'INSERT INTO public."user" (id, email, hashed_password, is_active, is_superuser, is_verified, '
        "company_id, role, password_change_required) "
        "VALUES (:id, :email, :hashed, true, true, false, :company_id, 'admin', :forced)"),
        {"id": user_id, "email": email, "hashed": PasswordHelper().hash(password),
         "company_id": company_id, "forced": forced})
    if provisioned:
        await session.execute(
            text("SELECT public.record_company_lifecycle(:company_id, 'create', :actor, CAST(:detail AS jsonb))"),
            {"company_id": company_id, "actor": user_id, "detail": json.dumps({"by": "first_run"})})
    await session.commit()
    created = {"email": email, "user_id": str(user_id), "company_id": str(company_id),
               "tenant_provisioned": provisioned, "password_change_required": forced}
    # Never the password (ADR-0004 dec 7); the default is documented, a chosen one is the deployment's.
    logger.warning("first-run superuser created: " + " ".join(f"{k}={v}" for k, v in created.items()))
    return created
