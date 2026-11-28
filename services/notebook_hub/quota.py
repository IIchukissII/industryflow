# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's cap on notebook environments running at once (ADR-0049 dec 1, 3, 4; ADR-0011 dec 7).

The hub is the one service that starts environments, so it counts from its own record: every server
of a user it holds that is running, or starting past its check, whose tenant is the one asking. A
count from that record cannot drift as a counter kept beside it could. The cap is the tenant's
override, which the alert worker copies into the capability store on each tenant-status tick
(``quotas.cap_key``): a store the hub trusts and the device-facing ingestion service cannot reach. Else
it is the platform default from ``TENANT_QUOTA_NOTEBOOK_ENVIRONMENTS``; an override the store cannot
give takes the default. A cap governs new starts only: lowering it stops nothing that runs (dec 3).

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


class NoRoom(Exception):
    """The notebook pool cannot guarantee one more environment its profile's measured need beside those
    holding a place (ADR-0018 dec 6)."""

    def __init__(self, share: int, held: int, need: int):
        mib = 1024 * 1024
        # What the user sees names no other tenant's use; the hub's log carries what is held.
        if share == 0:
            message = ("This host's memory is taken by the platform's own services; it has none for notebook "
                       "environments. Ask your platform operator.")
        else:
            message = (f"This host's {share // mib} MiB for notebook environments is held by the environments "
                       f"running; a new one needs {need // mib} MiB of it. Try again when one stops, or ask your "
                       "platform operator.")
        super().__init__(message)
        self.share, self.held, self.need = share, held, need


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


def _counts(spawner) -> bool:
    """Whether a server holds a place: running, or starting past its own check. A start still waiting
    on its check holds none, so two starts at the cap's edge do not refuse each other."""
    return spawner.active and (bool(spawner.ready) or getattr(spawner, "_quota_admitted", False))


async def running_for(this_spawner, company_id: str) -> int:
    """How many servers other than ``this_spawner`` hold a place for ``company_id``."""
    count = 0
    for user in list(this_spawner.user.settings["users"].values()):
        for spawner in list(user.spawners.values()):
            if spawner is this_spawner or not _counts(spawner):
                continue
            if await _tenant_of(spawner) == company_id:
                count += 1
    return count


def memory_held(this_spawner) -> int:
    """The guarantees, in bytes, of every server other than ``this_spawner`` that holds a place, of any
    tenant: what the notebook pool already guarantees."""
    held = 0
    for user in list(this_spawner.user.settings["users"].values()):
        for spawner in list(user.spawners.values()):
            if spawner is not this_spawner and _counts(spawner):
                held += int(getattr(spawner, "guarantee", 0) or 0)
    return held


# One check at a time: the hub is one process, and a count taken while another start is between its
# count and its admission would let both through.
_ADMISSION = asyncio.Lock()


async def admit(spawner, company_id: str, override_store, count_store,
                *, need: int, limit: int, share: Optional[int] = None) -> None:
    """Raise ``AtCap`` when the tenant already holds its cap of environments, recording the refusal in
    ``count_store``; with a notebook pool ``share`` (bytes, a Docker host's: ADR-0018 dec 6), raise
    ``NoRoom`` when the guarantees of the servers holding a place and this one's ``need`` exceed it; else
    mark the start as holding a place, with its guarantee and its memory ``limit``. The cap is read from
    ``override_store``."""
    spawner._quota_admitted = False
    async with _ADMISSION:
        cap = await asyncio.to_thread(tenant_cap, override_store, company_id)
        if cap is None or await running_for(spawner, company_id) < cap:
            if share is not None:
                held = memory_held(spawner)
                # A start of no guarantee would hold no place in the pool: never admitted against it.
                if need <= 0 or held + need > share:
                    raise NoRoom(share, held, need)
            # The place and its guarantee are taken together, under the lock, so the next check counts both.
            spawner.guarantee = need
            spawner.mem_limit = limit
            spawner._quota_admitted = True
            return
    await asyncio.to_thread(_record_refusal, count_store, company_id, time.time())
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
