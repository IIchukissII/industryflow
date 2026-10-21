# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which tenants ingestion refuses because they are suspended (ADR-0033 dec 3).

Ingestion reaches only the broker (ADR-0002 dec 3), so the registry's view arrives as the
tenant-status stream the alert worker publishes (``industryflow_contracts.tenant_status``): every
tenant, every few seconds. This follows it from the newest offset, as a consumer outside any group,
so every replica sees every message and none commits.

The stream sheds load; it does not enforce. A status not heard again within
``tenant_status.STALE_AFTER_SECONDS`` is forgotten, and a tenant never heard of is let through, so a
stopped publisher can neither strand a reactivated tenant nor halt ingestion. The writers of tenant
data refuse a suspended tenant from the registry itself.
"""
import asyncio
import logging
import time

from industryflow_contracts import tenant_status
from prometheus_client import Counter

logger = logging.getLogger(__name__)

REFUSED = Counter(
    "ingestion_suspended_tenant_refusals_total",
    "Readings refused because the tenant-status stream holds their tenant as suspended",
)


class TenantStatus:
    """The last status heard for each tenant, with when it was heard."""

    def __init__(self, stale_after: float = tenant_status.STALE_AFTER_SECONDS, clock=time.monotonic):
        self._stale_after = stale_after
        self._clock = clock
        self._heard: dict[str, tuple[bool, float]] = {}

    def apply(self, value: bytes) -> None:
        try:
            company_id, active = tenant_status.decode(value)
        except ValueError:
            logger.warning("ignored a message on the tenant-status stream that is not a status")
            return
        self._heard[company_id] = (active, self._clock())

    def is_suspended(self, company_id: str) -> bool:
        heard = self._heard.get(company_id)
        if heard is None:
            return False
        active, at = heard
        if self._clock() - at > self._stale_after:
            del self._heard[company_id]
            return False
        return not active


status = TenantStatus()


async def follow(consumer_factory, retry_seconds: float = 5.0) -> None:
    """Apply every message of the stream to ``status``, reconnecting after a broker failure.

    ``consumer_factory()`` returns an unstarted consumer of the tenant-status topic."""
    while True:
        consumer = consumer_factory()
        try:
            await consumer.start()
            logger.info("following the tenant-status stream")
            async for message in consumer:
                status.apply(message.value)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - any broker failure: reconnect and keep shedding stale
            logger.error("tenant-status stream lost: %s", e)
        finally:
            try:
                await consumer.stop()
            except Exception:  # noqa: BLE001 - already broken
                pass
        await asyncio.sleep(retry_seconds)
