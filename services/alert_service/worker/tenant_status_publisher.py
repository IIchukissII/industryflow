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

Each tenant's quota overrides ride its status (ADR-0049 dec 2): ingestion reads them from the stream,
and the hub, which is off the broker, from an expiring copy in the shared store (``quotas.cap_key``)
that this writes on the same tick. Both expire as a status goes stale, so a stopped publisher returns
every tenant to the platform defaults. An override the round cannot read leaves the status itself to
go out: suspension must not wait on a quota.
"""
import asyncio
import logging

from industryflow_contracts import quotas, tenancy, tenant_status

logger = logging.getLogger(__name__)

# Every registered tenant and whether it is active. The worker's role may read only these two
# registry columns (24-writers-read-tenant-status.sql).
REGISTRY_STATUS_SELECT = """
    SELECT schema_name, is_active
    FROM public.companies
    WHERE schema_name LIKE $1
"""


# Every tenant's quota overrides; the worker's role may read only these three columns (38-tenant-quotas.sql).
OVERRIDES_SELECT = "SELECT company_id, cap, value FROM public.tenant_quota_overrides"


async def registry_overrides(pool) -> dict:
    """``{company_id: {cap: value}}`` for every override of a cap this version knows."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(OVERRIDES_SELECT)
    held: dict = {}
    for row in rows:
        try:
            company_id = tenancy.canonical_company_id(row["company_id"])
            held.setdefault(company_id, {})[quotas.check_cap(row["cap"])] = quotas.check_value(row["value"])
        except ValueError:
            continue
    return held


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


async def publish_once(pool, send, topic: str, store=None) -> int:
    """Send one status message per tenant, with its overrides, and copy the overrides to ``store`` for
    the hub; returns how many statuses were sent."""
    status = await registry_status(pool)
    try:
        overrides = await registry_overrides(pool)
    except Exception as e:  # noqa: BLE001 - the statuses go out without the overrides
        logger.error("tenant quota overrides unreadable; statuses sent without them: %s", e)
        overrides = {}
    for company_id, active in status:
        key, value = tenant_status.encode(company_id, active, overrides.get(company_id))
        await send(topic, value=value, key=key)
    if store is not None and overrides:
        try:
            async with store.pipeline(transaction=False) as pipe:
                for company_id, caps in overrides.items():
                    for cap, value in caps.items():
                        pipe.set(quotas.cap_key(cap, company_id), value, ex=tenant_status.STALE_AFTER_SECONDS)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001 - the hub falls back to the defaults as the copies expire
            logger.error("tenant quota overrides not copied to the shared store: %s", e)
    return len(status)


async def run(pool, send, topic: str, interval: float, store=None):
    """Publish the whole registry every ``interval`` seconds. A failed round is logged and retried on
    the next tick: the writers refuse a suspended tenant from the registry meanwhile."""
    while True:
        try:
            count = await publish_once(pool, send, topic, store)
            logger.debug("published the status of %d tenants to %s", count, topic)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - one failed round must not end the publisher
            logger.error("tenant-status publish failed: %s", e)
        await asyncio.sleep(interval)
