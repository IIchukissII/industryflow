# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's SQL load through the proxy (ADR-0049 dec 1, 3, 4, 8, 9, 10).

Three caps hold per tenant: its sessions open at once, the seconds one statement may run, and the
seconds a transaction may stay idle. Each is the tenant's override, which the alert worker copies into
the capability store on every tenant-status tick (``quotas.cap_key``), else the platform default from
``TENANT_QUOTA_<CAP>``; an override the store cannot give takes the default. The kernel drives its own
session and could lift any limit set inside it, so the proxy holds all three from outside (dec 9): it
counts sessions here, and ``load.SessionLoad`` times statements and idle transactions on the wire.

A session holds a lease in two sorted sets of the quota store, the tenant's and every tenant's together,
scored by the lease's expiry (``quotas.sql_sessions_key``, ``quotas.SQL_SESSIONS_ALL_KEY``). Every proxy
replica counts in the same sets (dec 4); an open session renews its lease on each capability re-check,
and one whose proxy stopped without ending it leaves the count when its lease runs out. The total is the
notebook share of the database's connections (``NOTEBOOK_SQL_SESSIONS_TOTAL``, dec 10).

When the quota store cannot be reached the session is admitted without its count (dec 8), and the
failure is counted in the capability store, which the proxy reaches whenever it serves a session at all
and the API gateway reports on the platform plane: the proxy exports no metrics of its own, since
kernels reach it. A refusal is counted per tenant in the quota store, for a superuser to read through
the API gateway, and in a count with no tenant that the gateway reports (dec 5).
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Optional

from industryflow_contracts import quotas
from redis.exceptions import NoScriptError

log = logging.getLogger("notebook_sql_proxy")

CAPS = (quotas.SQL_SESSIONS, quotas.SQL_STATEMENT_SECONDS, quotas.SQL_IDLE_TRANSACTION_SECONDS)

# A session's lease: renewed on every capability re-check (SQL_PROXY_REVALIDATE_SECONDS, 15 s by default),
# so it outlives several missed renewals and a stopped proxy's sessions leave the count within it.
LEASE_SECONDS = 60

# How long the proxy waits on the quota store before it admits a session without its count (dec 8): a
# stalled store must not hold every new session, as ingestion's readings are not held (0.5 s there too).
STORE_TIMEOUT_SECONDS = 0.5


def check_renewal(revalidate_seconds: float) -> float:
    """The re-check interval, if a lease renewed at it cannot lapse while its session lives: at most half
    the lease, so one missed renewal still leaves the lease in force. ``ValueError`` otherwise."""
    if not 0 < revalidate_seconds <= LEASE_SECONDS / 2:
        raise ValueError(f"SQL_PROXY_REVALIDATE_SECONDS must be more than 0 and at most {LEASE_SECONDS / 2:g}: "
                         f"it renews each session's {LEASE_SECONDS} s lease in the SQL session count")
    return revalidate_seconds


def quota_store_client(url: str):
    """The quota store's client: one that gives up within ``STORE_TIMEOUT_SECONDS``."""
    import redis.asyncio as aioredis

    return aioredis.from_url(url, socket_connect_timeout=STORE_TIMEOUT_SECONDS,
                             socket_timeout=STORE_TIMEOUT_SECONDS)

# Admits a session if its tenant and every tenant together are under their caps, in one atomic step, so
# two replicas at the edge cannot both take the last place. Expired leases are dropped first.
#   KEYS: the tenant's set, every tenant's set
#   ARGV: now, lease expiry, session id, the tenant's cap (-1 off), the total, the sets' expiry
# Returns 0 admitted, 1 the tenant is at its cap, 2 every tenant together is at the total.
ADMIT_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[1])
local cap = tonumber(ARGV[4])
if cap >= 0 and redis.call('ZCARD', KEYS[1]) >= cap then return 1 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[5]) then return 2 end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[3])
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[6])
redis.call('EXPIRE', KEYS[2], ARGV[6])
return 0
"""
ADMIT_SCRIPT_SHA = hashlib.sha1(ADMIT_SCRIPT.encode()).hexdigest()

@dataclass(frozen=True)
class Limits:
    """The caps that hold for one tenant's session; ``None`` is off."""

    sessions: Optional[int]
    statement_seconds: Optional[int]
    idle_transaction_seconds: Optional[int]


class AtCap(Exception):
    """A session refused at a cap; the message names the cap and is what the kernel is told."""

    def __init__(self, cap: str, limit: int):
        if cap == quotas.SQL_SESSIONS:
            message = (f"Your organisation already has {limit} SQL session(s) open, its limit "
                       f"({cap}); close one or ask your platform operator to raise the limit.")
        else:
            message = ("The platform's SQL sessions for notebooks are all in use "
                       f"({quotas.SQL_SESSIONS_TOTAL_ENV}={limit}); try again shortly, or ask your "
                       "platform operator.")
        super().__init__(message)
        self.cap, self.limit = cap, limit


def settings(environ: Mapping[str, str]) -> tuple[dict, int]:
    """The platform defaults of the SQL caps and the total of every tenant's sessions, from the
    configuration; ``ValueError`` on a value the contract cannot read, so the proxy does not start."""
    defaults = quotas.defaults(environ)
    return {cap: defaults[cap] for cap in CAPS}, quotas.sql_sessions_total(environ)


class Lease:
    """One session's place in the count; renewed while the session lives, released when it ends."""

    def __init__(self, store, company_id: str, session_id: str):
        self._store = store
        self._keys = (quotas.sql_sessions_key(company_id), quotas.SQL_SESSIONS_ALL_KEY)
        self._session_id = session_id

    async def renew(self, now: Optional[float] = None) -> None:
        """Push the lease's expiry on; a failure only lets it run out early, which admits more (dec 8)."""
        expiry = (time.time() if now is None else now) + LEASE_SECONDS
        try:
            async with self._store.pipeline(transaction=False) as pipe:
                for key in self._keys:
                    pipe.zadd(key, {self._session_id: expiry}, xx=True)
                    pipe.expire(key, LEASE_SECONDS)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001 - the session goes on; its lease may lapse
            log.warning("SQL session lease not renewed: %s", e)

    async def release(self) -> None:
        try:
            async with self._store.pipeline(transaction=False) as pipe:
                for key in self._keys:
                    pipe.zrem(key, self._session_id)
                await pipe.execute()
        except Exception as e:  # noqa: BLE001 - the lease runs out on its own
            log.warning("SQL session lease not released; it runs out within %d s: %s", LEASE_SECONDS, e)


class _Unleased:
    """The place of a session admitted while the count could not be read: nothing to renew or release."""

    async def renew(self, now: Optional[float] = None) -> None:
        return None

    async def release(self) -> None:
        return None


class SessionQuota:
    """The SQL caps of a tenant and the count of its sessions, for every session the proxy opens."""

    def __init__(self, capability_store, quota_store, defaults: Mapping[str, Optional[int]], total: int):
        self._capabilities = capability_store
        self._count = quota_store
        self._defaults = dict(defaults)
        self._total = total

    async def limits(self, company_id: str) -> Limits:
        """The tenant's caps: each its override from the capability store, else the platform default."""
        values = {cap: await self._cap(cap, company_id) for cap in CAPS}
        return Limits(values[quotas.SQL_SESSIONS], values[quotas.SQL_STATEMENT_SECONDS],
                      values[quotas.SQL_IDLE_TRANSACTION_SECONDS])

    async def _cap(self, cap: str, company_id: str) -> Optional[int]:
        try:
            raw = await self._capabilities.get(quotas.cap_key(cap, company_id))
        except Exception as e:  # noqa: BLE001 - the default holds while the store cannot answer
            log.warning("tenant quota override unreadable, the default holds: %s", e)
            return self._defaults[cap]
        if raw is None:
            return self._defaults[cap]
        try:
            return quotas.check_value(int(raw))
        except (TypeError, ValueError):
            return self._defaults[cap]

    async def open(self, company_id: str, cap: Optional[int], now: Optional[float] = None):
        """A lease for one more session of the tenant under ``cap``; ``AtCap`` when it or the total is
        reached. A store that cannot be read admits the session without a lease (dec 8)."""
        now = time.time() if now is None else now
        session_id = uuid.uuid4().hex
        keys = (quotas.sql_sessions_key(company_id), quotas.SQL_SESSIONS_ALL_KEY)
        args = (now, now + LEASE_SECONDS, session_id, -1 if cap is None else cap, self._total, LEASE_SECONDS)
        try:
            try:
                verdict = await self._count.evalsha(ADMIT_SCRIPT_SHA, 2, *keys, *args)
            except NoScriptError:
                verdict = await self._count.eval(ADMIT_SCRIPT, 2, *keys, *args)
        except Exception as e:  # noqa: BLE001 - any store failure admits the session (dec 8)
            log.warning("SQL session count unavailable, session admitted without its cap: %s", e)
            await self._count_unavailable()
            return _Unleased()
        if int(verdict) == 0:
            return Lease(self._count, company_id, session_id)
        if int(verdict) == 1:
            await self.record_refusal(quotas.SQL_SESSIONS, company_id, now)
            raise AtCap(quotas.SQL_SESSIONS, cap)
        await self._record_total_refusal()
        raise AtCap(quotas.SQL_SESSIONS_TOTAL_ENV, self._total)

    async def _count_unavailable(self) -> None:
        try:
            await self._capabilities.incr(quotas.count_unavailable_key(quotas.SQL_SESSIONS))
        except Exception as e:  # noqa: BLE001 - logged above; nothing else to count it in
            log.error("SQL session count unavailable and not counted: %s", e)

    async def record_refusal(self, cap: str, company_id: str, now: Optional[float] = None) -> None:
        """Count a refusal at ``cap`` against the tenant and in the tenantless total: a session refused, a
        statement cancelled or a session ended. A failure loses only the count."""
        now = time.time() if now is None else now
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
        keep = quotas.REFUSALS_KEEP_DAYS * 86400
        try:
            async with self._count.pipeline(transaction=False) as pipe:
                pipe.hincrby(quotas.refusals_key(cap, day), company_id, 1)
                pipe.expire(quotas.refusals_key(cap, day), keep)
                pipe.hset(quotas.refused_last_key(cap, day), company_id, int(now))
                pipe.expire(quotas.refused_last_key(cap, day), keep)
                pipe.incr(quotas.refusals_total_key(cap))
                await pipe.execute()
        except Exception as e:  # noqa: BLE001 - the refusal itself stands
            log.warning("SQL quota refusal at %s not recorded: %s", cap, e)

    async def _record_total_refusal(self) -> None:
        try:
            await self._count.incr(quotas.SQL_SESSIONS_TOTAL_REFUSALS_KEY)
        except Exception as e:  # noqa: BLE001 - the refusal itself stands
            log.warning("SQL session total refusal not recorded: %s", e)
