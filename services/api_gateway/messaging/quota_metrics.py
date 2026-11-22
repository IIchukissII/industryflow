# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quota refusals on the platform plane (ADR-0049 dec 5).

Ingestion and the hub add every refusal to a count with no tenant in the shared store
(``quotas.refusals_total_key``). Each gateway worker reads those counts every ``EVERY_SECONDS`` into
``tenant_quota_refusals``, one series per cap and never a tenant (ADR-0016 dec 5), which the operator's
alert reads by its growth. Which tenants were refused, a superuser reads at GET
/api/platform/quotas/refusals. A count lost with the store restarts from zero; a fall is no refusal.
"""
import asyncio
import logging

from industryflow_contracts import quotas
from prometheus_client import Gauge

logger = logging.getLogger(__name__)

EVERY_SECONDS = 15

REFUSALS = Gauge("tenant_quota_refusals",
                 "Demands refused at a tenant quota cap, as the shared count holds them (ADR-0049); per cap",
                 ["cap"])


async def read_once(store) -> None:
    for cap in quotas.CAPS:
        REFUSALS.labels(cap=cap).set(int(await store.get(quotas.refusals_total_key(cap)) or 0))


async def run(store) -> None:
    while True:
        try:
            await read_once(store)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - the next pass reads what this one missed
            logger.warning("tenant quota refusals not read: %s", e)
        await asyncio.sleep(EVERY_SECONDS)
