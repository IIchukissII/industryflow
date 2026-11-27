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

The stored-data caps hold only on a fresh measurement (ADR-0049 dec 8, 11): every pass also counts the
tenants, provisioned longer ago than a measurement may be old, whose data in either store has no
measurement that fresh (``tenant_storage_unmeasured``, no tenant), which the operator's alert reads.
"""
import asyncio
import logging

import os

from industryflow_contracts import quotas
from prometheus_client import Gauge
from sqlalchemy import text

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

UNMEASURED = Gauge("tenant_storage_unmeasured",
                   "Tenants whose stored data has no fresh measurement in a store, so no stored-data cap "
                   "holds for them (ADR-0049 dec 8, 11)")

USAGE_STALE_SECONDS = quotas.usage_stale_seconds(os.environ)

# Tenants not deleted, older than the stale bound, without a measurement of both stores within it. Read
# and run as written by infrastructure/timescaledb/tests/test_tenant_quotas.py.
UNMEASURED_SELECT = (
    "SELECT count(*) AS unmeasured FROM public.companies c "
    "WHERE c.deleted_at IS NULL AND c.created_at < now() - make_interval(secs => :stale) "
    "AND (SELECT count(*) FROM public.tenant_storage_usage u WHERE u.company_id = c.company_id "
    "AND u.measured_at >= now() - make_interval(secs => :stale)) < 2"
)


async def read_unmeasured(db) -> None:
    row = (await db.execute(text(UNMEASURED_SELECT), {"stale": USAGE_STALE_SECONDS})).mappings().one()
    UNMEASURED.set(int(row["unmeasured"]))


async def read_once(store, capabilities=None) -> None:
    for cap in quotas.CAPS:
        REFUSALS.labels(cap=cap).set(int(await store.get(quotas.refusals_total_key(cap)) or 0))
    TOTAL_REFUSALS.set(int(await store.get(quotas.SQL_SESSIONS_TOTAL_REFUSALS_KEY) or 0))
    if capabilities is not None:
        COUNT_UNAVAILABLE.labels(cap=quotas.SQL_SESSIONS).set(
            int(await capabilities.get(quotas.count_unavailable_key(quotas.SQL_SESSIONS)) or 0))


async def run(store) -> None:
    from database import AsyncSessionLocal

    while True:
        try:
            async with AsyncSessionLocal() as db:
                await read_unmeasured(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the next pass reads what this one missed
            logger.warning("tenant storage measurements not read: %s", e)
        try:
            await read_once(store, redis_client.redis)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the next pass reads what this one missed
            logger.warning("tenant quota refusals not read: %s", e)
        await asyncio.sleep(EVERY_SECONDS)
