# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quotas (ADR-0049): a platform superuser reads the caps and overrides one for one tenant.

Each cap's platform default is the deployment's configuration (``TENANT_QUOTA_<CAP>``, read once at
start through the shared contract, so a mistyped value stops the gateway). An override is written only
through ``set_tenant_quota()``, which records the change in the same transaction
(infrastructure/timescaledb/init-scripts/38-tenant-quotas.sql); the gateway holds no write privilege on
the overrides. The alert worker carries them to ingestion and the hub within one tenant-status interval.

Which tenants a cap refused is read here, from the shared store's per-day counts, and nowhere on the
platform plane, whose metrics count refusals per cap only (ADR-0049 dec 5; ADR-0016 dec 5).
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from industryflow_contracts import quotas

from database import get_db
from dependencies import require_superuser
from models.user import User

router = APIRouter(prefix="/api/platform/quotas", tags=["tenant quotas"])

# The platform defaults, read once: a value the contract cannot read stops the gateway at import.
DEFAULTS: Dict[str, Optional[int]] = quotas.defaults()

# Read and run as written by infrastructure/timescaledb/tests/test_tenant_quotas.py.
OVERRIDES_SELECT = (
    "SELECT company_id, cap, value, updated_at, updated_by FROM public.tenant_quota_overrides "
    "WHERE (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid)) "
    "ORDER BY company_id, cap"
)
QUOTA_SET = (
    "SELECT changed, value, updated_at "
    "FROM public.set_tenant_quota(:company_id, :cap, CAST(:value AS integer), :actor_id)"
)
CHANGES_SELECT = (
    "SELECT change_id, company_id, cap, old_value, new_value, changed_by, changed_at "
    "FROM public.tenant_quota_changes WHERE company_id = :company_id "
    "AND (CAST(:before_id AS bigint) IS NULL OR change_id < CAST(:before_id AS bigint)) "
    "ORDER BY change_id DESC LIMIT :limit"
)

# The database's refusals: an unknown cap or value (22023), no such tenant (P0002).
_INVALID = "22023"
_NO_TENANT = "P0002"


class Override(BaseModel):
    company_id: UUID
    cap: str
    value: int
    updated_at: datetime
    updated_by: UUID


class Quotas(BaseModel):
    # A cap's platform default; null is off (ADR-0049 dec 6).
    defaults: Dict[str, Optional[int]]
    overrides: List[Override]


class TenantCap(BaseModel):
    default: Optional[int]
    override: Optional[int]
    effective: Optional[int]


class TenantQuotas(BaseModel):
    company_id: UUID
    caps: Dict[str, TenantCap]


class SetQuota(BaseModel):
    # A whole number, or null to return the tenant to the platform default. Strict: "5" or 5.0 is
    # refused rather than read as a count.
    value: Optional[StrictInt] = Field(..., ge=1, le=quotas.MAX_VALUE)


class SetResult(BaseModel):
    company_id: UUID
    cap: str
    changed: bool
    override: Optional[int]
    effective: Optional[int]
    updated_at: datetime


class Change(BaseModel):
    change_id: int
    company_id: UUID
    cap: str
    old_value: Optional[int]
    new_value: Optional[int]
    changed_by: UUID
    changed_at: datetime


class Refusals(BaseModel):
    cap: str
    company_id: UUID
    refusals: int
    last_refused_at: Optional[datetime]


def _cap(cap: str) -> str:
    try:
        return quotas.check_cap(cap)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"No tenant quota {cap!r}")


async def _overrides(db: AsyncSession, company_id: Optional[UUID]) -> List[Override]:
    rows = (await db.execute(text(OVERRIDES_SELECT),
                             {"company_id": str(company_id) if company_id else None})).mappings().all()
    return [Override(**row) for row in rows]


@router.get("", response_model=Quotas)
async def list_quotas(user: User = Depends(require_superuser), db: AsyncSession = Depends(get_db)):
    """The platform defaults and every tenant's overrides."""
    return Quotas(defaults=DEFAULTS, overrides=await _overrides(db, None))


@router.get("/tenants/{company_id}", response_model=TenantQuotas)
async def tenant_quotas(company_id: UUID, user: User = Depends(require_superuser),
                        db: AsyncSession = Depends(get_db)):
    """The caps that hold for one tenant: each default, override and the effective value."""
    held = {o.cap: o.value for o in await _overrides(db, company_id)}
    return TenantQuotas(company_id=company_id, caps={
        cap: TenantCap(default=DEFAULTS[cap], override=held.get(cap),
                       effective=quotas.effective(DEFAULTS[cap], held.get(cap)))
        for cap in quotas.CAPS
    })


@router.put("/tenants/{company_id}/{cap}", response_model=SetResult)
async def set_quota(company_id: UUID, cap: str, body: SetQuota, user: User = Depends(require_superuser),
                    db: AsyncSession = Depends(get_db)):
    """Set the tenant's override of ``cap``, or clear it with null; an unchanged value records nothing."""
    cap = _cap(cap)
    try:
        row = (await db.execute(text(QUOTA_SET), {
            "company_id": company_id, "cap": cap, "value": body.value, "actor_id": UUID(str(user.id)),
        })).mappings().one()
        await db.commit()
    except DBAPIError as exc:
        await db.rollback()
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if code == _NO_TENANT:
            raise HTTPException(status_code=404, detail=f"No tenant {company_id}")
        if code == _INVALID:
            raise HTTPException(status_code=422, detail="Not a tenant quota or not a value it takes")
        raise
    return SetResult(company_id=company_id, cap=cap, changed=row["changed"], override=row["value"],
                     effective=quotas.effective(DEFAULTS[cap], row["value"]), updated_at=row["updated_at"])


@router.get("/tenants/{company_id}/changes", response_model=List[Change])
async def list_changes(company_id: UUID,
                       before_id: Optional[int] = Query(None, ge=1, le=2**63 - 1,
                                                        description="Page: changes older than this change_id"),
                       limit: int = Query(50, ge=1, le=500),
                       user: User = Depends(require_superuser), db: AsyncSession = Depends(get_db)):
    """The tenant's overrides set and cleared, newest first (ADR-0049 dec 2)."""
    rows = (await db.execute(text(CHANGES_SELECT), {
        "company_id": company_id, "before_id": before_id, "limit": limit,
    })).mappings().all()
    return [Change(**row) for row in rows]


def _store():
    """The shared store holding the refusal counts (isolated for tests)."""
    from messaging.redis_client import redis_client

    return redis_client.redis


@router.get("/refusals", response_model=List[Refusals])
async def list_refusals(days: int = Query(7, ge=1, le=quotas.REFUSALS_KEEP_DAYS),
                        user: User = Depends(require_superuser)):
    """Which tenants each cap refused over the last ``days`` (UTC days, today included), most first."""
    store = _store()
    today = datetime.now(timezone.utc).date()
    out: List[Refusals] = []
    for cap in quotas.CAPS:
        counts: Dict[str, int] = {}
        for back in range(days):
            day = (today - timedelta(days=back)).strftime("%Y%m%d")
            for company_id, n in (await store.hgetall(quotas.refusals_key(cap, day))).items():
                counts[company_id] = counts.get(company_id, 0) + int(n)
        last = await store.hgetall(quotas.refused_last_key(cap))
        for company_id, n in counts.items():
            at = last.get(company_id)
            out.append(Refusals(cap=cap, company_id=company_id, refusals=n,
                                last_refused_at=datetime.fromtimestamp(int(at), timezone.utc) if at else None))
    return sorted(out, key=lambda r: (-r.refusals, r.cap, str(r.company_id)))
