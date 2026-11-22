# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's cap on device readings per second (ADR-0049 dec 1, 3, 4, 8).

The cap is the tenant's override heard on the tenant-status stream, else the platform default from
``TENANT_QUOTA_READINGS_PER_SECOND``. Every replica counts in the shared store (ADR-0002 dec 3), one
key per tenant and second of the clock, so the cap holds across replicas. A reading over the cap is
refused before it is produced, so the device keeps it and sends it again (dec 3).

When the store cannot be reached the reading is admitted and the failure counted on this service's
metrics, which alerts the operator (dec 8): only the cap is waived, the reading's other checks still
hold. A refusal is counted in the shared store, per tenant for a superuser to read through the API
gateway, and in a count with no tenant that the gateway reports on the platform plane (dec 5).
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from industryflow_contracts import quotas
from prometheus_client import Counter

logger = logging.getLogger(__name__)

CAP = quotas.READINGS_PER_SECOND

COUNT_UNAVAILABLE = Counter(
    "tenant_quota_count_unavailable_total",
    "Demands admitted without their cap because the shared count could not be read (ADR-0049 dec 8)",
    ["cap"],
)


async def admit(store, company_id: str, cap: Optional[int], now: Optional[float] = None) -> bool:
    """Whether the tenant may produce one more reading this second under ``cap`` (``None`` is off)."""
    if cap is None:
        return True
    now = time.time() if now is None else now
    key = quotas.readings_key(company_id, int(now))
    try:
        async with store.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, quotas.READINGS_KEY_TTL_SECONDS)
            count, _ = await pipe.execute()
    except Exception as e:  # noqa: BLE001 - any store failure admits the reading (dec 8)
        COUNT_UNAVAILABLE.labels(cap=CAP).inc()
        logger.warning("quota count unavailable, reading admitted without its cap: %s", e)
        return True
    if int(count) <= cap:
        return True
    await _record_refusal(store, company_id, now)
    return False


async def _record_refusal(store, company_id: str, now: float) -> None:
    """Count the refusal against the tenant and in the tenantless total; a failure loses only the count."""
    day_key = quotas.refusals_key(CAP, datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d"))
    try:
        async with store.pipeline(transaction=False) as pipe:
            pipe.hincrby(day_key, company_id, 1)
            pipe.expire(day_key, quotas.REFUSALS_KEEP_DAYS * 86400)
            pipe.hset(quotas.refused_last_key(CAP), company_id, int(now))
            pipe.incr(quotas.refusals_total_key(CAP))
            await pipe.execute()
    except Exception as e:  # noqa: BLE001 - the refusal itself stands
        logger.warning("quota refusal not recorded for the tenant: %s", e)
