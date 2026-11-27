# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quota refusals on the platform plane (ADR-0049 dec 5, 8, 10).

Ingestion, the hub and the SQL proxy add every refusal to a count with no tenant in the quota store
(``quotas.refusals_total_key``). Each gateway worker reads those counts every ``EVERY_SECONDS`` into
``tenant_quota_refusals``, one series per cap and never a tenant (ADR-0016 dec 5), which the operator's
alert reads by its growth. Which tenants were refused, a superuser reads at GET
/api/platform/quotas/refusals. A count lost with the store restarts from zero; a fall is no refusal.

Two counts come from the SQL proxy, which exports no metrics of its own because kernels reach it:
refusals at the total of every tenant's SQL sessions (``tenant_quota_total_refusals``, from the quota
store), and sessions admitted while the quota store could not be read (``tenant_quota_count_unavailable``,
from the capability store, since the quota store is the one that failed).
"""
import asyncio
import logging

from industryflow_contracts import quotas
from prometheus_client import Gauge

from messaging.redis_client import redis_client

logger = logging.getLogger(__name__)

EVERY_SECONDS = 15

REFUSALS = Gauge("tenant_quota_refusals",
                 "Demands refused at a tenant quota cap, as the quota store holds them (ADR-0049); per cap",
                 ["cap"])


TOTAL_REFUSALS = Gauge("tenant_quota_total_refusals",
                       "SQL sessions refused at the total of every tenant's sessions (ADR-0049 dec 10)")

COUNT_UNAVAILABLE = Gauge("tenant_quota_count_unavailable",
                          "SQL sessions admitted without their cap because the quota store could not be "
                          "read, as the capability store holds them (ADR-0049 dec 8); per cap",
                          ["cap"])

async def read_once(store, capabilities=None) -> None:
    for cap in quotas.CAPS:
        REFUSALS.labels(cap=cap).set(int(await store.get(quotas.refusals_total_key(cap)) or 0))
    TOTAL_REFUSALS.set(int(await store.get(quotas.SQL_SESSIONS_TOTAL_REFUSALS_KEY) or 0))
    if capabilities is not None:
        COUNT_UNAVAILABLE.labels(cap=quotas.SQL_SESSIONS).set(
            int(await capabilities.get(quotas.count_unavailable_key(quotas.SQL_SESSIONS)) or 0))


async def run(store) -> None:
    while True:
        try:
            await read_once(store, redis_client.redis)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the next pass reads what this one missed
            logger.warning("tenant quota refusals not read: %s", e)
        await asyncio.sleep(EVERY_SECONDS)
