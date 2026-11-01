# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Password guessing is slowed per account and source, never locked out (ADR-0004 dec 10).

Failed password sign-ins are counted per e-mail address and source address in the shared store. From
``THRESHOLD`` failures on, each attempt from that source must wait twice as long after the last as the
one before, up to ``MAX_DELAY_SECONDS``; an attempt inside the wait is refused (429) without the
password being checked, and is not counted. One source slowing an account does not slow the account's
owner at another, so no one can lock a user out; each source is also limited across all accounts at
the edge.

Each attempt is reserved before the password is checked, atomically (``RESERVE_SCRIPT``): it is
counted as a failure up front, so guesses sent at the same moment are refused all but one, and a
success clears the count. A password change or reset clears every source's count for the address,
and an hour without a failure forgets it.

The key is a hash of the address as submitted, normalised, so an unknown address is counted exactly
like a known one and the answer reveals nothing about which accounts exist.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import time

from industryflow_contracts.sessions import READ_TIMEOUT_SECONDS

THRESHOLD = 5
MAX_DELAY_SECONDS = 300
FORGET_AFTER_SECONDS = 3600
_PREFIX = "sign-in-failures:"

# KEYS[1] the address's record; ARGV: source, now, threshold, cap, forget. Returns 0 when the attempt
# may go ahead (and is now counted), else the whole seconds still to wait.
RESERVE_SCRIPT = """
local n = tonumber(redis.call('HGET', KEYS[1], 'n:' .. ARGV[1]) or '0')
local at = tonumber(redis.call('HGET', KEYS[1], 'at:' .. ARGV[1]) or '0')
local now, threshold, cap = tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
if n >= threshold then
  local remaining = at + math.min(2 ^ (n - threshold), cap) - now
  if remaining > 0 then return math.ceil(remaining) end
end
redis.call('HINCRBY', KEYS[1], 'n:' .. ARGV[1], 1)
redis.call('HSET', KEYS[1], 'at:' .. ARGV[1], ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 0
"""


def _key(email: str) -> str:
    return _PREFIX + hashlib.sha256((email or "").strip().lower().encode()).hexdigest()


# How the gateway counts (SIGN_IN_THROTTLE): per account and source, where it is told each client's
# own address by the edge; off where it is not (behind a proxy that hides them, ADR-0004 Deferred),
# since every client would then share one source and the count would hold the owner out.
MODES = ("per-source", "off")


def check_configuration(mode: str, edge_peers) -> None:
    """Refuses a start in which the throttle could not tell clients apart."""
    if mode not in MODES:
        raise RuntimeError(f"SIGN_IN_THROTTLE must be one of {MODES}, not {mode!r}")
    if mode == "per-source" and not (edge_peers.networks or edge_peers.names):
        raise RuntimeError("SIGN_IN_THROTTLE=per-source needs EDGE_PROXY_PEERS: without the edge's address "
                           "every client's source is the edge, and one source's failures would hold the owner out")


def _source(source) -> str:
    """The counted source: an IPv4 address, or an IPv6 address's /64 (one host holds a whole one)."""
    text = str(source or "unknown")[:64]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return text
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def delay_after(failures: int) -> int:
    """How long the next attempt from a source must wait after ``failures`` consecutive failures."""
    if failures < THRESHOLD:
        return 0
    return min(2 ** (failures - THRESHOLD), MAX_DELAY_SECONDS)


async def reserve(store, email: str, source) -> int:
    """0 when this attempt may check the password (it is counted until ``succeeded``), else the
    seconds to wait. A store error, or a store slower than the read timeout, propagates."""
    waited = await asyncio.wait_for(
        store.eval(RESERVE_SCRIPT, 1, _key(email), _source(source), repr(time.time()), THRESHOLD,
                   MAX_DELAY_SECONDS, FORGET_AFTER_SECONDS),
        READ_TIMEOUT_SECONDS)
    return int(waited)


async def succeeded(store, email: str, source) -> None:
    """The password was right and the sign-in admitted: this source's count starts again."""
    src = _source(source)
    await asyncio.wait_for(store.hdel(_key(email), f"n:{src}", f"at:{src}"), READ_TIMEOUT_SECONDS)


async def cleared(store, email: str) -> None:
    """A new password: no source's failures count against it."""
    await asyncio.wait_for(store.delete(_key(email)), READ_TIMEOUT_SECONDS)
