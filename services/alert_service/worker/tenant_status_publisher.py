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
and the hub and the SQL proxy, which are off the broker, from an expiring copy (``quotas.cap_key``) that
this writes on the same tick to the capability store, which they trust and the device-facing ingestion
service cannot reach (ADR-0033 dec 3). Both expire as a status goes stale, so a stopped publisher returns every
tenant to the platform defaults; a cleared override's copy is deleted on the next tick. A round that
cannot read the overrides sends the last ones read: suspension must not wait on a quota.

Each status also names the stored-data caps its tenant is over (ADR-0049 dec 11, 12): the tenant's last
measurement in each store against the cap that holds for it. Ingestion refuses that tenant's readings
from the stream; the tracking gateway, off the broker, refuses its artifact writes from an expiring copy
(``quotas.over_key``) in the capability store. A measurement older than ``TENANT_USAGE_STALE_SECONDS``,
or one that cannot be read, holds nothing (dec 8); the API gateway alerts on its age.
"""
import asyncio
import logging

from config import config
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

# Every tenant's last measurement in each store, and its age by the database's clock (38-tenant-quotas.sql).
USAGE_SELECT = """
    SELECT company_id, store, bytes, EXTRACT(EPOCH FROM now() - measured_at)::float8 AS age
    FROM public.tenant_storage_usage
"""

# The platform defaults of the stored-data caps and how old a measurement may be, from the worker's
# configuration (config.py, the one reader of its environment).
DEFAULTS = {quotas.STORED_DATABASE_GIB: config.TENANT_QUOTA_STORED_DATABASE_GIB,
            quotas.STORED_OBJECT_GIB: config.TENANT_QUOTA_STORED_OBJECT_GIB}
USAGE_STALE_SECONDS = config.TENANT_USAGE_STALE_SECONDS


async def registry_usage(pool) -> dict:
    """``{company_id: {store: (bytes, age_seconds)}}`` for every measurement of a store this version knows."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(USAGE_SELECT)
    held: dict = {}
    for row in rows:
        if row["store"] not in quotas.STORED_CAPS:
            continue
        try:
            company_id = tenancy.canonical_company_id(row["company_id"])
        except ValueError:
            continue
        held.setdefault(company_id, {})[row["store"]] = (int(row["bytes"]), float(row["age"]))
    return held


def over_caps(usage: dict, overrides: dict) -> frozenset:
    """The stored-data caps a tenant is over, from its measurements (bytes, age) and its overrides."""
    caps = {cap: quotas.effective(DEFAULTS[cap], overrides.get(cap)) for cap in quotas.STORED_CAPS.values()}
    # Ages are the database's, so the measurement's time is its age before a "now" of zero.
    measured = {store: (size, -age) for store, (size, age) in usage.items()}
    return quotas.stored_over(measured, caps, now=0.0, stale_after=USAGE_STALE_SECONDS)


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


# The last overrides read, sent again while a read fails, so one unreadable round does not drop every
# tenant to the defaults; and the copies written for the hub, so a cleared override's copy is deleted.
_last_overrides: dict = {}
_copied: set = set()


async def publish_once(pool, send, topic: str, store=None) -> int:
    """Send one status message per tenant, with its overrides, and copy the overrides to ``store`` for
    the hub; returns how many statuses were sent."""
    global _last_overrides
    status = await registry_status(pool)
    try:
        overrides = _last_overrides = await registry_overrides(pool)
    except Exception as e:  # noqa: BLE001 - the statuses go out with the last overrides read
        logger.error("tenant quota overrides unreadable; the last ones read are sent: %s", e)
        overrides = _last_overrides
    try:
        usage = await registry_usage(pool)
    except Exception as e:  # noqa: BLE001 - no measurement holds (dec 8); the gateway alerts on its age
        logger.error("tenant storage measurements unreadable; no stored-data cap holds this round: %s", e)
        usage = {}
    registered = {company_id for company_id, _active in status}
    over = {company_id: over_caps(held, overrides.get(company_id, {}))
            for company_id, held in usage.items() if company_id in registered}
    for company_id, active in status:
        key, value = tenant_status.encode(company_id, active, overrides.get(company_id),
                                          over.get(company_id, ()))
        await send(topic, value=value, key=key)
    if store is not None:
        await _copy_for_the_hub(store, overrides, over)
    return len(status)


async def _copy_for_the_hub(store, overrides: dict, over: dict | None = None) -> None:
    """Write each override's expiring copy, and each stored-data cap a tenant is over, for the hub, the
    SQL proxy and the tracking gateway; delete the copy of one cleared since the last round."""
    global _copied
    keys = {quotas.cap_key(cap, company_id): value
            for company_id, caps in overrides.items() for cap, value in caps.items()}
    keys.update({quotas.over_key(cap, company_id): 1
                 for company_id, caps in (over or {}).items() for cap in caps})
    try:
        async with store.pipeline(transaction=False) as pipe:
            for key, value in keys.items():
                pipe.set(key, value, ex=tenant_status.STALE_AFTER_SECONDS)
            for gone in _copied - keys.keys():
                pipe.delete(gone)
            await pipe.execute()
        _copied = set(keys)
    except Exception as e:  # noqa: BLE001 - the hub falls back to the defaults as the copies expire
        logger.error("tenant quota overrides not copied for the hub: %s", e)


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
