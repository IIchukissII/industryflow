# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's cap on device readings per second (ADR-0049 dec 1, 3, 4, 8).

The cap is the tenant's override heard on the tenant-status stream, else the platform default from
``TENANT_QUOTA_READINGS_PER_SECOND``. Every replica counts in the quota store (ADR-0002 dec 3), one
key per tenant and second of the clock, so the cap holds across replicas. A reading over the cap is
refused before it is produced, so the device keeps it and sends it again (dec 3).

When the store cannot be reached the reading is admitted and the failure counted on this service's
metrics, which alerts the operator (dec 8): only the cap is waived, the reading's other checks still
hold. A refusal is counted in the quota store, per tenant for a superuser to read through the API
gateway, and in a count with no tenant that the gateway reports on the platform plane (dec 5).
"""
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from industryflow_contracts import quotas
from prometheus_client import Counter
from redis.exceptions import NoScriptError

logger = logging.getLogger(__name__)

CAP = quotas.READINGS_PER_SECOND

COUNT_UNAVAILABLE = Counter(
    "tenant_quota_count_unavailable_total",
    "Demands admitted without their cap because the quota store could not be read (ADR-0049 dec 8)",
    ["cap"],
)


# One reading counted in one command: the script runs atomically, so the second's key never exists
# without its expiry, and a reading costs one round trip with no transaction around it.
COUNT_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""
COUNT_SCRIPT_SHA = hashlib.sha1(COUNT_SCRIPT.encode()).hexdigest()


async def _count(store, key: str) -> int:
    """The reading's place in its second: the script by its digest, loaded on the first miss."""
    try:
        return await store.evalsha(COUNT_SCRIPT_SHA, 1, key, quotas.READINGS_KEY_TTL_SECONDS)
    except NoScriptError:
        return await store.eval(COUNT_SCRIPT, 1, key, quotas.READINGS_KEY_TTL_SECONDS)


async def admit(store, company_id: str, cap: Optional[int], now: Optional[float] = None) -> bool:
    """Whether the tenant may produce one more reading this second under ``cap`` (``None`` is off)."""
    if cap is None:
        return True
    now = time.time() if now is None else now
    key = quotas.readings_key(company_id, int(now))
    try:
        count = await _count(store, key)
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
        logger.warning("quota refusal not recorded for the tenant: %s", e)
