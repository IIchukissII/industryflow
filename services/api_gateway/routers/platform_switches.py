# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Platform switches (ADR-0043): a platform superuser reads and flips them; no tenant role can.

The only switch is ADR-0024's stateful-feature kill switch. The gateway flips it through
``set_platform_switch()``, which accepts a known switch and a value of its type and records the flip
in the same transaction (infrastructure/timescaledb/init-scripts/32-platform-switch-control.sql); the
gateway holds no write privilege on the switches. The ML service reads the switch within its cache
lifetime (``STATEFUL_SWITCH_CACHE_TTL``).
"""
from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, StrictBool
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_superuser
from models.user import User

router = APIRouter(prefix="/api/platform/switches", tags=["platform switches"])

# Read and run as written by infrastructure/timescaledb/tests/test_platform_switch_control.py.
SWITCHES_SELECT = (
    "SELECT key, value, description, updated_at, updated_by FROM public.platform_config ORDER BY key"
)
SWITCH_FLIP = "SELECT changed, value, updated_at FROM public.set_platform_switch(:key, CAST(:value AS jsonb), :actor_id)"
CHANGES_SELECT = (
    "SELECT change_id, key, old_value, new_value, changed_by, changed_at "
    "FROM public.platform_config_changes WHERE key = :key "
    "AND (CAST(:before_id AS bigint) IS NULL OR change_id < CAST(:before_id AS bigint)) "
    "ORDER BY change_id DESC LIMIT :limit"
)

# The database's refusal of a switch it does not know (the value's type is checked by ``Flip``).
_UNKNOWN_SWITCH = "22023"


class Switch(BaseModel):
    key: str
    value: Any
    description: Optional[str] = None
    updated_at: datetime
    updated_by: Optional[str] = None


class Flip(BaseModel):
    # Strict: "off", 0 or "no" is refused rather than read as a value for a platform-wide switch.
    value: StrictBool


class FlipResult(BaseModel):
    key: str
    value: Any
    changed: bool
    updated_at: datetime


class Change(BaseModel):
    change_id: int
    key: str
    old_value: Any
    new_value: Any
    changed_by: UUID
    changed_at: datetime


@router.get("", response_model=List[Switch])
async def list_switches(user: User = Depends(require_superuser), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(text(SWITCHES_SELECT))).mappings().all()
    return [Switch(**row) for row in rows]


@router.put("/{key}", response_model=FlipResult)
async def flip_switch(key: str, body: Flip, user: User = Depends(require_superuser),
                      db: AsyncSession = Depends(get_db)):
    """Set the switch; a value equal to the current one changes and records nothing."""
    try:
        row = (await db.execute(text(SWITCH_FLIP), {
            "key": key, "value": "true" if body.value else "false", "actor_id": UUID(str(user.id)),
        })).mappings().one()
        await db.commit()
    except DBAPIError as exc:
        await db.rollback()
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if code == _UNKNOWN_SWITCH:
            raise HTTPException(status_code=404, detail=f"No platform switch {key!r}")
        raise
    return FlipResult(key=key, value=row["value"], changed=row["changed"], updated_at=row["updated_at"])


@router.get("/{key}/changes", response_model=List[Change])
async def list_changes(key: str,
                       before_id: Optional[int] = Query(None, ge=1, le=2**63 - 1,
                                                        description="Page: changes older than this change_id"),
                       limit: int = Query(50, ge=1, le=500),
                       user: User = Depends(require_superuser), db: AsyncSession = Depends(get_db)):
    """The switch's flips, newest first (ADR-0043 dec 4)."""
    rows = (await db.execute(text(CHANGES_SELECT),
                             {"key": key, "before_id": before_id, "limit": limit})).mappings().all()
    return [Change(**row) for row in rows]
