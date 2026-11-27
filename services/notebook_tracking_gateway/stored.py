# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant over its object-store cap is refused its artifact writes (ADR-0049 dec 11, 12).

The alert worker measures nothing; it compares each tenant's last measurement with the cap that holds
for it and copies the caps a tenant is over into the capability store (``quotas.over_key``), which this
gateway already reads to resolve every handle. A write is refused while the copy is there, naming the
cap; reads and deletes go on, and nothing is deleted to bring the tenant under its cap (dec 13). A copy
that cannot be read refuses nothing (dec 8).

A refusal is counted per tenant in the quota store, for a superuser to read through the API gateway,
and in the count with no tenant the gateway reports on the platform plane (dec 5); a store that cannot
take it loses only the count.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from industryflow_contracts import quotas

logger = logging.getLogger(__name__)

CAP = quotas.STORED_OBJECT_GIB

# How long a client refused for the cap waits before it tries again.
RETRY_AFTER_SECONDS = 300


async def refuse_if_over(capabilities, quota_store, company_id: str) -> None:
    """``HTTPException`` 507 naming the cap when the tenant is over its object-store cap."""
    try:
        over = await capabilities.get(quotas.over_key(CAP, company_id))
    except Exception as e:  # noqa: BLE001 - the cap is waived while its copy cannot be read (dec 8)
        logger.warning("stored-data cap unreadable, the write is admitted: %s", e)
        return
    if over is None:
        return
    await _record_refusal(quota_store, company_id, time.time())
    raise HTTPException(status_code=507, detail=f"Tenant over its stored-data quota ({CAP})",
                        headers={"Retry-After": str(RETRY_AFTER_SECONDS)})


async def _record_refusal(store: Optional[object], company_id: str, now: float) -> None:
    if store is None:
        return
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
    keep = quotas.REFUSALS_KEEP_DAYS * 86400
    try:
        async with store.pipeline(transaction=False) as pipe:
            pipe.hincrby(quotas.refusals_key(CAP, day), company_id, 1)
            pipe.expire(quotas.refusals_key(CAP, day), keep)
            pipe.hset(quotas.refused_last_key(CAP, day), company_id, int(now))
            pipe.expire(quotas.refused_last_key(CAP, day), keep)
            pipe.incr(quotas.refusals_total_key(CAP))
            await pipe.execute()
    except Exception as e:  # noqa: BLE001 - the refusal itself stands
        logger.warning("stored-data refusal not recorded: %s", e)
