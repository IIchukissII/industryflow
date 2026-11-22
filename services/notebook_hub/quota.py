# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's cap on notebook environments running at once (ADR-0049 dec 1, 3, 4; ADR-0011 dec 7).

The hub is the one service that starts environments, so it counts from its own record: every server
of a user it holds that is running or starting, whose tenant is the one asking. A count from that
record cannot drift as a counter kept beside it could. The cap is the tenant's override, which the
alert worker copies into the quota store on each tenant-status tick (``quotas.cap_key``), else the
platform default from ``TENANT_QUOTA_NOTEBOOK_ENVIRONMENTS``; an override the store cannot give takes
the default. A cap governs new starts only: lowering it stops nothing that runs (dec 3).

A refusal is counted per tenant in the quota store, which a superuser reads through the API gateway,
and in a count with no tenant, which the gateway reports on the platform plane (dec 5): the hub's own
metrics are reachable from kernels, so it exports none of its own.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from industryflow_contracts import quotas

logger = logging.getLogger(__name__)

CAP = quotas.NOTEBOOK_ENVIRONMENTS

# The platform default, read once: a value the contract cannot read stops the hub at start.
DEFAULT: Optional[int] = quotas.defaults(os.environ)[CAP]


class AtCap(Exception):
    """The tenant runs as many environments as its cap allows."""

    def __init__(self, cap: int):
        super().__init__(f"Your organisation already runs {cap} notebook environment(s), its limit; "
                         "stop one or ask your platform operator to raise the limit.")
        self.cap = cap


def tenant_cap(store, company_id: str) -> Optional[int]:
    """The cap that holds for the tenant: its override from the quota store, else the default."""
    try:
        raw = store.get(quotas.cap_key(CAP, company_id))
    except Exception as e:  # noqa: BLE001 - the default holds while the store cannot answer
        logger.warning("tenant quota override unreadable, the default holds: %s", e)
        return DEFAULT
    if raw is None:
        return DEFAULT
    try:
        return quotas.check_value(int(raw))
    except (TypeError, ValueError):
        return DEFAULT


async def _tenant_of(spawner) -> str:
    """The tenant a server runs for: the one it was started for, else its user's current one."""
    started_for = getattr(spawner, "_audit_company_id", None)
    if started_for:
        return started_for
    return (await spawner.user.get_auth_state() or {}).get("company_id", "")


async def running_for(this_spawner, company_id: str) -> int:
    """How many servers other than ``this_spawner`` run or are starting for ``company_id``."""
    count = 0
    for user in list(this_spawner.user.settings["users"].values()):
        for spawner in list(user.spawners.values()):
            if spawner is this_spawner or not spawner.active:
                continue
            if await _tenant_of(spawner) == company_id:
                count += 1
    return count


async def admit(spawner, company_id: str, store) -> None:
    """Raise ``AtCap`` when the tenant already runs its cap of environments; record the refusal."""
    cap = await asyncio.to_thread(tenant_cap, store, company_id)
    if cap is None or await running_for(spawner, company_id) < cap:
        return
    await asyncio.to_thread(_record_refusal, store, company_id, time.time())
    raise AtCap(cap)


def _record_refusal(store, company_id: str, now: float) -> None:
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
    keep = quotas.REFUSALS_KEEP_DAYS * 86400
    try:
        pipe = store.pipeline(transaction=False)
        pipe.hincrby(quotas.refusals_key(CAP, day), company_id, 1)
        pipe.expire(quotas.refusals_key(CAP, day), keep)
        pipe.hset(quotas.refused_last_key(CAP, day), company_id, int(now))
        pipe.expire(quotas.refused_last_key(CAP, day), keep)
        pipe.incr(quotas.refusals_total_key(CAP))
        pipe.execute()
    except Exception as e:  # noqa: BLE001 - the refusal itself stands
        logger.warning("notebook quota refusal not recorded: %s", e)
