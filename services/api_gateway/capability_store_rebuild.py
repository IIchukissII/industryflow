# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The capability store's rebuild: every denial it can re-derive, then the record that it was rebuilt
(ADR-0015 dec 8, ADR-0032 dec 1).

The store holds denials whose absence grants (``industryflow_contracts.denials``). Every pass marks
each company the registry holds as inactive suspended, then writes the rebuild record if the store has
none: last, so a verifier that sees the record sees the markers. The record's time ends every session
issued before it (``industryflow_contracts.sessions``), since the ended-session records have no source
outside the store. A store recreated empty under running services is rebuilt within one pass; until
then every verifier refuses.

Markers are only added here, never removed: a suspension in flight writes its marker after its
registry commit, and removing markers by an earlier read could undo it. Reactivation removes its own
marker after its commit; a marker this pass wrote for a company reactivated meanwhile is removed by
re-reading the registry for exactly the companies it marked. Every replica runs this; each step is
idempotent, and the record is written once (``SET NX``).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List

from industryflow_contracts import denials
from prometheus_client import Counter, Gauge
from sqlalchemy import bindparam, text

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 10.0

INACTIVE_COMPANIES = text("SELECT company_id FROM public.companies WHERE NOT is_active")
ACTIVE_AMONG = text(
    "SELECT company_id FROM public.companies WHERE is_active AND company_id IN :ids"
).bindparams(bindparam("ids", expanding=True))

# The store's rebuild time as every pass reads it, so a rebuild shows whichever replica wrote it.
REBUILT_AT = Gauge("capability_store_rebuilt_timestamp_seconds",
                   "When the capability store was last rebuilt (its denials re-derived, every earlier session ended)")
MARKERS_RESTORED = Counter("capability_store_suspension_markers_restored_total",
                           "Suspension markers the rebuild found missing and wrote")


async def _ids(engine, statement, **params) -> List[str]:
    async with engine.begin() as conn:
        rows = (await conn.execute(statement, params)).fetchall()
    return [str(row.company_id) for row in rows]


async def rebuild_once(redis, engine) -> bool:
    """One pass. Returns True when this pass wrote the rebuild record."""
    inactive = await _ids(engine, INACTIVE_COMPANIES)
    missing = []
    if inactive:
        present = await redis.mget([denials.suspended_key(c) for c in inactive])
        missing = [c for c, value in zip(inactive, present) if value is None]
    for company_id in missing:
        await redis.set(denials.suspended_key(company_id), "1")
    if missing:
        MARKERS_RESTORED.inc(len(missing))
        logger.warning("capability store: restored %d suspension marker(s)", len(missing))
        for company_id in await _ids(engine, ACTIVE_AMONG, ids=missing):
            await redis.delete(denials.suspended_key(company_id))
    wrote = bool(await redis.set(denials.STORE_REBUILT_KEY, repr(time.time()), nx=True))
    REBUILT_AT.set(denials.rebuilt_at(await redis.get(denials.STORE_REBUILT_KEY)))
    if wrote:
        logger.warning("capability store had no rebuild record: denials re-derived, every earlier session "
                       "ended (ADR-0015 dec 8)")
    return wrote


async def run(redis, engine, interval: float = INTERVAL_SECONDS) -> None:
    """Rebuild passes for the life of the process; a failed pass is retried at the next one."""
    while True:
        try:
            await rebuild_once(redis, engine)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the store or registry is out; the next pass retries
            logger.error("capability store rebuild pass failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)
