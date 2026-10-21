# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Publishes every registered tenant's status on the tenant-status stream (ADR-0033 dec 3).

Ingestion reaches only the broker and refuses a suspended tenant's devices from this stream. The
alert worker publishes it because it already holds both the registry read and the broker, so no
component gains reach it did not have: the API gateway is kept off the broker, and ingestion off
the registry and the capability store. The whole registry goes out every ``interval`` seconds
(``industryflow_contracts.tenant_status``), on its own timer rather than the rule reload's, so a
suspension reaches ingestion within one interval.
"""
import asyncio
import logging

from industryflow_contracts import tenancy, tenant_status

logger = logging.getLogger(__name__)

# Every registered tenant and whether it is active. The worker's role may read only these two
# registry columns (24-writers-read-tenant-status.sql).
REGISTRY_STATUS_SELECT = """
    SELECT schema_name, is_active
    FROM public.companies
    WHERE schema_name LIKE $1
"""


async def registry_status(pool) -> list[tuple[str, bool]]:
    """``[(company_id, active)]`` for every registry entry whose schema name is exactly a tenant's."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(REGISTRY_STATUS_SELECT, tenancy.SCHEMA_LIKE)
    status = []
    for row in rows:
        try:
            status.append((tenancy.company_id_from_schema(row["schema_name"]), bool(row["is_active"])))
        except ValueError:
            continue
    return status


async def publish_once(pool, send, topic: str) -> int:
    """Send one status message per tenant; returns how many were sent."""
    status = await registry_status(pool)
    for company_id, active in status:
        key, value = tenant_status.encode(company_id, active)
        await send(topic, value=value, key=key)
    return len(status)


async def run(pool, send, topic: str, interval: float):
    """Publish the whole registry every ``interval`` seconds. A failed round is logged and retried on
    the next tick: the writers refuse a suspended tenant from the registry meanwhile."""
    while True:
        try:
            count = await publish_once(pool, send, topic)
            logger.debug("published the status of %d tenants to %s", count, topic)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - one failed round must not end the publisher
            logger.error("tenant-status publish failed: %s", e)
        await asyncio.sleep(interval)
