# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Password guessing is slowed per account, never locked out (ADR-0004 dec 10).

Consecutive failed password sign-ins for one e-mail address are counted in the shared store. From
``THRESHOLD`` failures on, the next attempt must wait twice as long as the one before, up to
``MAX_DELAY_SECONDS``; an attempt inside the wait is refused (429) without the password being checked.
A success, a password change or a reset clears the count, and an hour without a failure forgets it.

The key is a hash of the address as submitted, normalised, so an unknown address is counted exactly
like a known one and the answer reveals nothing about which accounts exist. Each source address is
limited separately at the edge.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time

from industryflow_contracts.sessions import READ_TIMEOUT_SECONDS

THRESHOLD = 5
MAX_DELAY_SECONDS = 300
FORGET_AFTER_SECONDS = 3600
_PREFIX = "sign-in-failures:"


def _key(email: str) -> str:
    return _PREFIX + hashlib.sha256((email or "").strip().lower().encode()).hexdigest()


def delay_after(failures: int) -> int:
    """How long the next attempt must wait after ``failures`` consecutive failures."""
    if failures < THRESHOLD:
        return 0
    return min(2 ** (failures - THRESHOLD), MAX_DELAY_SECONDS)


async def retry_after(store, email: str) -> int:
    """Seconds until the address may try again; 0 when it may now. A store error propagates."""
    record = await asyncio.wait_for(store.hgetall(_key(email)), READ_TIMEOUT_SECONDS)
    if not record:
        return 0
    record = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
              for k, v in record.items()}
    try:
        failures, last = int(record.get("n", 0)), float(record.get("at", 0))
    except ValueError:
        return 0
    remaining = last + delay_after(failures) - time.time()
    return math.ceil(remaining) if remaining > 0 else 0


async def failed(store, email: str) -> None:
    key = _key(email)
    async with store.pipeline(transaction=True) as pipe:
        pipe.hincrby(key, "n", 1)
        pipe.hset(key, "at", repr(time.time()))
        pipe.expire(key, FORGET_AFTER_SECONDS)
        await asyncio.wait_for(pipe.execute(), READ_TIMEOUT_SECONDS)


async def cleared(store, email: str) -> None:
    await asyncio.wait_for(store.delete(_key(email)), READ_TIMEOUT_SECONDS)
