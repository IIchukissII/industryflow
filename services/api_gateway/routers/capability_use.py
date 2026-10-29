# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability-use record, read (ADR-0042 dec 6).

A platform superuser reads every tenant's events, optionally narrowed to one; a tenant's administrator
reads their own tenant's only, whatever they ask for: the filter is the verified account's company,
never a parameter (ADR-0003 dec 1).
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_role
from models.user import User

router = APIRouter(prefix="/api/capability-use", tags=["capability use"])

CAPABILITY_USE_SELECT = (
    "SELECT event_id, kind, plane, at, company_id, user_id, audience, handle_id, digest, reason, detail "
    "FROM public.capability_use_events "
    "WHERE (CAST(:company_id AS uuid) IS NULL OR company_id = CAST(:company_id AS uuid)) "
    "AND (CAST(:handle_id AS uuid) IS NULL OR handle_id = CAST(:handle_id AS uuid)) "
    "AND (CAST(:since AS timestamptz) IS NULL OR at >= CAST(:since AS timestamptz)) "
    "AND (CAST(:before_id AS bigint) IS NULL OR event_id < CAST(:before_id AS bigint)) "
    "ORDER BY event_id DESC LIMIT :limit"
)


@router.get("")
async def list_capability_use(
    company_id: Optional[UUID] = Query(None, description="Superuser only: one tenant's events"),
    handle_id: Optional[UUID] = Query(None, description="One capability's events"),
    since: Optional[datetime] = Query(None, description="Events at or after this time"),
    before_id: Optional[int] = Query(None, description="Page: events older than this event_id"),
    limit: int = Query(100, ge=1, le=1000),
    user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Newest first. A tenant administrator's ``company_id`` is their own, whatever was passed."""
    if user.is_superuser:
        scope = company_id
    elif user.company_id:
        scope = user.company_id
    else:
        # No company would mean no filter, which is every tenant's record.
        raise HTTPException(status_code=403, detail="User has no company association")
    rows = (await db.execute(text(CAPABILITY_USE_SELECT), {
        "company_id": UUID(str(scope)) if scope else None,
        "handle_id": handle_id,
        # A datetime, not its text: asyncpg binds the cast parameter as a timestamptz.
        "since": since,
        "before_id": before_id,
        "limit": limit,
    })).mappings().all()
    return {"events": [
        {k: (str(v) if isinstance(v, UUID) else v.isoformat() if isinstance(v, datetime) else v)
         for k, v in row.items()}
        for row in rows
    ]}
