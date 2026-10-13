# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Companies (tenants) for the admin panel.

The gateway's DB role may only read the tenant registry. Writes go through the SECURITY DEFINER
functions of 21-company-provisioning.sql, which validate the input and create the tenant schema;
who may call them is decided here (ADR-0004 dec 5):

- create: superuser only. Creates the company and its tenant schema.
- update: a tenant admin may rename their own company; (de)activating one is superuser-only.
- delete: superuser only, and it DEACTIVATES. The company and all of its users are made inactive
  and their sessions revoked; the schema and its data stay. Nothing in the API drops a tenant.

Every transition to inactive — an update that clears the flag, or a delete — then takes the same
path (``_suspended``): the company is marked suspended in the capability store, so no notebook
handle bound to it resolves or is minted anywhere, and every session of every one of its users is
ended, with the notebook capabilities derived from them (ADR-0004 dec 2 rev 2, ADR-0033 dec 3).
From then on its users are refused at authentication (company_status.py). Reactivating removes the
marker; it does not restore any session.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from uuid import UUID
import logging

import company_status
from dependencies import get_db_with_tenant, require_role
from messaging import refresh_tokens
from messaging.redis_client import redis_client
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/companies", tags=["companies"])

# The registry's own limit (public.companies.company_name VARCHAR(255)); the DB re-checks it.
CompanyName = Field(min_length=1, max_length=255)


class CompanyCreate(BaseModel):
    company_name: str = CompanyName
    is_active: bool = True


class CompanyUpdate(BaseModel):
    company_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    is_active: Optional[bool] = None


class CompanyResponse(BaseModel):
    company_id: UUID
    company_name: str
    is_active: bool
    created_at: datetime


def _require_superuser(current_user: User) -> None:
    """Provisioning a tenant (create/delete a company) requires a superuser (ADR-0004)."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser privileges required")


def _assert_own_company(current_user: User, company_id: UUID) -> None:
    """
    A non-superuser admin may only act on their own company (ADR-0004 decision 5).
    Returns 404 rather than 403 so other tenants' company IDs are not confirmed.
    """
    if not current_user.is_superuser and str(current_user.company_id) != str(company_id):
        raise HTTPException(status_code=404, detail="Company not found")


def _sqlstate(e: DBAPIError) -> Optional[str]:
    return getattr(e.orig, "sqlstate", None) or getattr(e.orig, "pgcode", None)


def _provisioning_error(e: DBAPIError) -> HTTPException:
    """Map what the provisioning functions refuse to a client answer; anything else is a 500."""
    state = _sqlstate(e)
    if state == "23505":  # unique_violation: the name is taken
        return HTTPException(status_code=409, detail="A company with this name already exists")
    if state == "23514":  # check_violation: the name failed the registry's validation
        return HTTPException(status_code=422, detail="Invalid company name")
    logger.error(f"Company provisioning failed: {e}")
    return HTTPException(status_code=500, detail="Company provisioning failed")


async def _company_user_ids(db: AsyncSession, company_id: UUID) -> list:
    result = await db.execute(
        text('SELECT id FROM public."user" WHERE company_id = :company_id'),
        {"company_id": company_id},
    )
    return [r.id for r in result.fetchall()]


def _audit(action: str, company_id, actor: User, **details) -> None:
    """One structured record per lifecycle action: who did what to which company (ADR-0033 dec 6)."""
    record = {"action": action, "company_id": str(company_id), "actor": str(actor.id), **details}
    logger.info(
        "company lifecycle " + " ".join(f"{k}={v}" for k, v in record.items()),
        extra={"company_lifecycle": record},
    )


async def _suspended(db: AsyncSession, company_id: UUID, user_ids: list) -> None:
    """After a transition to inactive has committed: end the tenant's open SQL sessions, refuse
    the company's capabilities everywhere, then end every session of its users and the
    capabilities derived from them.

    The SQL sessions are ended in a transaction of their own, after the suspension's commit made
    the tenant's login NOLOGIN: ended any earlier, a client could reconnect before that commit
    (21-company-provisioning.sql). Idempotent, so repeating the request completes a run that
    failed part way. The registry is already inactive by then, so authentication refuses the
    users meanwhile (company_status.py).
    """
    try:
        await db.execute(
            text("SELECT public.end_suspended_tenant_sql_sessions(:company_id)"),
            {"company_id": company_id},
        )
        await db.commit()
    except DBAPIError as e:
        raise _provisioning_error(e)
    company_status.forget(company_id)
    await company_status.mark_suspended(redis_client.redis, company_id)
    for user_id in user_ids:
        await refresh_tokens.end_all_sessions(str(user_id))


async def _reactivated(company_id: UUID) -> None:
    company_status.forget(company_id)
    await company_status.clear_suspended(redis_client.redis, company_id)


def _row(row) -> dict:
    return {
        "company_id": row.company_id,
        "company_name": row.company_name,
        "is_active": row.is_active,
        "created_at": row.created_at,
    }


@router.get("", response_model=List[CompanyResponse])
async def list_companies(
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """List companies. Superusers see all; a per-tenant admin sees only their own (ADR-0004)."""
    if current_user.is_superuser:
        result = await db.execute(text("""
            SELECT company_id, company_name, is_active, created_at
            FROM public.companies
            ORDER BY company_name
        """))
    else:
        result = await db.execute(text("""
            SELECT company_id, company_name, is_active, created_at
            FROM public.companies
            WHERE company_id = :company_id
            ORDER BY company_name
        """), {"company_id": current_user.company_id})

    return [_row(row) for row in result.fetchall()]


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """Get a single company by ID (own company only, unless superuser)."""
    _assert_own_company(current_user, company_id)
    result = await db.execute(
        text("""
            SELECT company_id, company_name, is_active, created_at
            FROM public.companies
            WHERE company_id = :company_id
        """),
        {"company_id": company_id}
    )

    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    return _row(row)


@router.post("", response_model=CompanyResponse, status_code=201)
async def create_company(
    company: CompanyCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """Create a company and its isolated tenant schema (superuser only — provisioning)."""
    _require_superuser(current_user)
    try:
        result = await db.execute(
            text("SELECT * FROM public.provision_company(:name, :is_active)"),
            {"name": company.company_name, "is_active": company.is_active},
        )
        row = result.fetchone()
        await db.commit()
    except DBAPIError as e:
        raise _provisioning_error(e)

    _audit("create", row.company_id, current_user)
    return _row(row)


@router.put("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    company: CompanyUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """Rename a company (own company only, unless superuser). (De)activating is superuser-only."""
    _assert_own_company(current_user, company_id)
    is_active = company.is_active
    if is_active is not None and not current_user.is_superuser:
        # The edit form sends the flag back unchanged; only an actual change is a privilege request.
        current = await db.execute(
            text("SELECT is_active FROM public.companies WHERE company_id = :company_id"),
            {"company_id": company_id},
        )
        current_row = current.fetchone()
        if current_row is not None and current_row.is_active == is_active:
            is_active = None
        else:
            _require_superuser(current_user)
    if company.company_name is None and is_active is None:
        raise HTTPException(status_code=400, detail="No fields to update")
    if is_active is False and str(current_user.company_id) == str(company_id):
        raise HTTPException(status_code=400, detail="Cannot deactivate your own company")

    try:
        result = await db.execute(
            text("SELECT * FROM public.update_company(:company_id, :name, :is_active)"),
            {"company_id": company_id, "name": company.company_name, "is_active": is_active},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Company not found")
        user_ids = await _company_user_ids(db, company_id) if is_active is False else []
        await db.commit()
    except DBAPIError as e:
        raise _provisioning_error(e)

    # Deactivating is suspending, by whichever route it is asked for: the same path as delete.
    if is_active is False:
        await _suspended(db, company_id, user_ids)
        _audit("deactivate", company_id, current_user, sessions_ended_for=len(user_ids))
    elif is_active is True:
        await _reactivated(company_id)
        _audit("reactivate", company_id, current_user)
    if company.company_name is not None:
        _audit("rename", company_id, current_user)
    return _row(row)


@router.delete("/{company_id}", response_model=CompanyResponse)
async def delete_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """
    Deactivate a company (superuser only). The company and every one of its users become
    inactive and the users' sessions are revoked; the tenant schema and its data are kept.
    """
    _require_superuser(current_user)
    if str(current_user.company_id) == str(company_id):
        raise HTTPException(status_code=400, detail="Cannot deactivate your own company")
    try:
        result = await db.execute(
            text("SELECT * FROM public.update_company(:company_id, NULL, false)"),
            {"company_id": company_id},
        )
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Company not found")
        users = await db.execute(
            text('UPDATE public."user" SET is_active = false WHERE company_id = :company_id RETURNING id'),
            {"company_id": company_id},
        )
        user_ids = [r.id for r in users.fetchall()]
        await db.commit()
    except DBAPIError as e:
        raise _provisioning_error(e)

    # After the commit: a deactivated user can no longer refresh even if this step fails part way
    # (refresh checks is_active), so revocation here only ends the sessions sooner.
    await _suspended(db, company_id, user_ids)
    _audit("delete", company_id, current_user, users_deactivated=len(user_ids))
    return _row(row)
