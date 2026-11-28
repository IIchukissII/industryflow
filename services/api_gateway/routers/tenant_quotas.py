# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quotas (ADR-0049): a platform superuser reads the caps and overrides one for one tenant.

Each cap's platform default is the deployment's configuration (``TENANT_QUOTA_<CAP>``, read once at
start through the shared contract, so a mistyped value stops the gateway): an amount, off, or a share of
what the overrides leave of a capacity the deployment derives (ADR-0049 dec 2, 14). An override is written
only through ``set_tenant_quota()``, which records the change in the same transaction and refuses one that
would take the overrides of a derived capacity past it
(infrastructure/timescaledb/init-scripts/38-tenant-quotas.sql); the gateway holds no write privilege on
the overrides. The alert worker carries them to ingestion and the hub within one tenant-status interval.

Which tenants a cap refused is read here, from the quota store's per-day counts, and nowhere on the
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

# The platform defaults as configured and the derived capacities, read once: a value the contract cannot
# read stops the gateway at import.
SETTINGS = quotas.settings()
CAPACITIES: Dict[str, int] = quotas.capacities()

# Read and run as written by infrastructure/timescaledb/tests/test_tenant_quotas.py.
OVERRIDES_SELECT = (
    "SELECT company_id, cap, value, updated_at, updated_by FROM public.tenant_quota_overrides "
    "WHERE (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid)) "
    "ORDER BY company_id, cap"
)
QUOTA_SET = (
    "SELECT changed, value, updated_at "
    "FROM public.set_tenant_quota(:company_id, :cap, CAST(:value AS integer), :actor_id, "
    "CAST(:capacity AS integer))"
)
# What the other tenants' overrides of a cap hold, named in a refusal at its capacity (dec 14).
OTHERS_SELECT = (
    "SELECT coalesce(sum(value), 0) AS others FROM public.tenant_quota_overrides "
    "WHERE cap = :cap AND company_id <> :company_id"
)
USAGE_SELECT = (
    "SELECT store, bytes, measured_at FROM public.tenant_storage_usage WHERE company_id = :company_id"
)
CHANGES_SELECT = (
    "SELECT change_id, company_id, cap, old_value, new_value, changed_by, changed_at "
    "FROM public.tenant_quota_changes WHERE company_id = :company_id "
    "AND (CAST(:before_id AS bigint) IS NULL OR change_id < CAST(:before_id AS bigint)) "
    "ORDER BY change_id DESC LIMIT :limit"
)

# The database's refusals: an unknown cap or value (22023), no such tenant (P0002), the overrides of a cap
# past its derived capacity (53400).
_INVALID = "22023"
_NO_TENANT = "P0002"
_PAST_CAPACITY = "53400"


class Override(BaseModel):
    company_id: UUID
    cap: str
    value: int
    updated_at: datetime
    updated_by: UUID


class Quotas(BaseModel):
    # A cap's platform default a tenant without an override takes now; null is off (ADR-0049 dec 6, 14).
    defaults: Dict[str, Optional[int]]
    # The caps whose default is a share, as a percentage of what the overrides leave of the capacity.
    shares: Dict[str, int] = {}
    # The capacities the deployment derives (ADR-0050 dec 4); a cap absent has none.
    capacities: Dict[str, int] = {}
    overrides: List[Override]


class TenantCap(BaseModel):
    default: Optional[int]
    override: Optional[int]
    effective: Optional[int]


class Stored(BaseModel):
    # The tenant's data in one store as last measured (ADR-0049 dec 11).
    bytes: int
    measured_at: datetime


class TenantQuotas(BaseModel):
    company_id: UUID
    caps: Dict[str, TenantCap]
    # By store ("database", "object"); a store not yet measured is absent.
    stored: Dict[str, Stored] = {}


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


def _defaults(every: List[Override]) -> Dict[str, Optional[int]]:
    """The default a tenant without an override takes now: an amount or off as configured, or its share of
    what every tenant's overrides leave of the cap's capacity (dec 14)."""
    reserved: Dict[str, int] = {}
    for o in every:
        reserved[o.cap] = reserved.get(o.cap, 0) + o.value
    return {cap: quotas.resolve(SETTINGS[cap], CAPACITIES.get(cap), reserved.get(cap, 0)) for cap in quotas.CAPS}


@router.get("", response_model=Quotas)
async def list_quotas(user: User = Depends(require_superuser), db: AsyncSession = Depends(get_db)):
    """The platform defaults, the shares and capacities they come from, and every tenant's overrides."""
    every = await _overrides(db, None)
    return Quotas(defaults=_defaults(every), overrides=every, capacities=CAPACITIES,
                  shares={cap: s.percent for cap, s in SETTINGS.items() if isinstance(s, quotas.Share)})


@router.get("/tenants/{company_id}", response_model=TenantQuotas)
async def tenant_quotas(company_id: UUID, user: User = Depends(require_superuser),
                        db: AsyncSession = Depends(get_db)):
    """The caps that hold for one tenant: each default, override and the effective value; and its data in
    each store as last measured."""
    every = await _overrides(db, None)
    defaults = _defaults(every)
    held = {o.cap: o.value for o in every if o.company_id == company_id}
    usage = (await db.execute(text(USAGE_SELECT), {"company_id": company_id})).mappings().all()
    return TenantQuotas(company_id=company_id, caps={
        cap: TenantCap(default=defaults[cap], override=held.get(cap),
                       effective=quotas.effective(defaults[cap], held.get(cap)))
        for cap in quotas.CAPS
    }, stored={row["store"]: Stored(bytes=row["bytes"], measured_at=row["measured_at"]) for row in usage})


@router.put("/tenants/{company_id}/{cap}", response_model=SetResult)
async def set_quota(company_id: UUID, cap: str, body: SetQuota, user: User = Depends(require_superuser),
                    db: AsyncSession = Depends(get_db)):
    """Set the tenant's override of ``cap``, or clear it with null; an unchanged value records nothing. An
    override that would take every tenant's overrides of a derived capacity past it is refused (dec 14)."""
    cap = _cap(cap)
    capacity = CAPACITIES.get(cap)
    try:
        row = (await db.execute(text(QUOTA_SET), {
            "company_id": company_id, "cap": cap, "value": body.value, "actor_id": UUID(str(user.id)),
            "capacity": capacity,
        })).mappings().one()
        await db.commit()
    except DBAPIError as exc:
        await db.rollback()
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if code == _NO_TENANT:
            raise HTTPException(status_code=404, detail=f"No tenant {company_id}")
        if code == _INVALID:
            raise HTTPException(status_code=422, detail="Not a tenant quota or not a value it takes")
        if code == _PAST_CAPACITY:
            others = int((await db.execute(text(OTHERS_SELECT), {"cap": cap, "company_id": company_id}))
                         .mappings().one()["others"])
            raise HTTPException(status_code=409, detail=(
                f"The deployment carries {capacity} for {cap}; the other tenants' overrides hold {others}, "
                f"so {max(capacity - others, 0)} is left"))
        raise
    defaults = _defaults(await _overrides(db, None))
    return SetResult(company_id=company_id, cap=cap, changed=row["changed"], override=row["value"],
                     effective=quotas.effective(defaults[cap], row["value"]), updated_at=row["updated_at"])


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
    """The shared count of tenant quotas, holding the refusal counts (isolated for tests)."""
    from messaging.quota_store import quota_store

    return quota_store()


@router.get("/refusals", response_model=List[Refusals])
async def list_refusals(days: int = Query(7, ge=1, le=quotas.REFUSALS_KEEP_DAYS),
                        user: User = Depends(require_superuser)):
    """Which tenants each cap refused over the last ``days`` (UTC days, today included), most first."""
    store = _store()
    today = datetime.now(timezone.utc).date()
    out: List[Refusals] = []
    for cap in quotas.CAPS:
        counts: Dict[str, int] = {}
        last: Dict[str, int] = {}
        for back in range(days):
            day = (today - timedelta(days=back)).strftime("%Y%m%d")
            for company_id, n in (await store.hgetall(quotas.refusals_key(cap, day))).items():
                counts[company_id] = counts.get(company_id, 0) + int(n)
            for company_id, at in (await store.hgetall(quotas.refused_last_key(cap, day))).items():
                last[company_id] = max(last.get(company_id, 0), int(at))
        for company_id, n in counts.items():
            at = last.get(company_id)
            out.append(Refusals(cap=cap, company_id=company_id, refusals=n,
                                last_refused_at=datetime.fromtimestamp(int(at), timezone.utc) if at else None))
    return sorted(out, key=lambda r: (-r.refusals, r.cap, str(r.company_id)))
