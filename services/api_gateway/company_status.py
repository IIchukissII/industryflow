# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether a company is active, as authentication reads it (ADR-0004 dec 2, ADR-0033 dec 3).

A suspended (inactive) company's users are refused at authentication: every session-authenticated
request, login, refresh, socket and capability-authenticated request checks the caller's company
here. The registry is the source of truth; this keeps a short per-process cache of it so that the
check is not a database round trip per request. A suspension is refused at once by every process:
the transition writes the suspension marker into the shared capability store (``mark_suspended``),
and every check reads it before the cache. COMPANY_STATUS_CACHE_SECONDS bounds only a change made
outside the API, and a reactivation, which is refused for that long at most, never admitted early;
with the store unreachable the check falls back to the cache and its bound.

The capability planes outside this service do not read the registry. For them the transition also
writes a suspension marker into the shared capability store (``mark_suspended``), which every
verifier and minter of capability handles consults (capability_auth.SUSPENDED_PREFIX, the shared
contract), so no handle for a suspended company resolves or is minted, at once and everywhere.
"""
import logging
import time
from collections import OrderedDict
from typing import Optional

from industryflow_contracts import tenancy
from sqlalchemy import text

from capability_auth import suspended_key
from config import get_settings

logger = logging.getLogger(__name__)

# Bounded: least recently checked company evicted first, so it cannot grow with the tenant count.
_CACHE_MAX_COMPANIES = 4096
_cache: "OrderedDict[str, tuple[bool, float]]" = OrderedDict()


def _session_factory():
    # Imported lazily: database.py builds the engine at import time.
    from database import AsyncSessionLocal

    return AsyncSessionLocal()


async def _read_is_active(company_id: str) -> bool:
    """The registry's flag. A company that does not exist is not active."""
    async with _session_factory() as session:
        result = await session.execute(
            text("SELECT is_active FROM public.companies WHERE company_id = :company_id"),
            {"company_id": company_id},
        )
        row = result.fetchone()
    return bool(row is not None and row.is_active)


def _canonical(company_id) -> Optional[str]:
    try:
        return tenancy.canonical_company_id(company_id)
    except (ValueError, TypeError):
        return None


def _store():
    # Imported lazily: the client connects at startup, and tests run without it.
    from messaging.redis_client import redis_client

    return redis_client.redis


async def _marked_suspended(company_id: str) -> bool:
    """Whether the shared store holds the company's suspension marker; False when the store cannot
    be read, which leaves the decision to the registry and its cache."""
    store = _store()
    if store is None:
        return False
    try:
        return bool(await store.exists(suspended_key(company_id)))
    except Exception as exc:  # noqa: BLE001 - an unreachable store must not refuse every request
        logger.warning("company status: suspension marker unreadable, using the registry: %s", exc)
        return False


async def is_company_active(company_id) -> bool:
    """True if the company exists and is active: not marked suspended in the shared store, and active
    in the registry, read through a cache of bounded staleness.

    A malformed identifier is not active. A database error propagates: an unknown answer is not a
    yes, and the request fails rather than being admitted.
    """
    key = _canonical(company_id)
    if key is None:
        return False
    if await _marked_suspended(key):
        return False
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[1] > now:
        _cache.move_to_end(key)
        return cached[0]
    active = await _read_is_active(key)
    _cache[key] = (active, now + get_settings().COMPANY_STATUS_CACHE_SECONDS)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_COMPANIES:
        _cache.popitem(last=False)
    return active


def forget(company_id) -> None:
    """Drop this process's cached flag for a company, after it changed here."""
    key = _canonical(company_id)
    if key is not None:
        _cache.pop(key, None)


def clear() -> None:
    _cache.clear()


async def mark_suspended(redis, company_id) -> None:
    """Record in the shared capability store that the company is suspended (no expiry: it lasts
    as long as the suspension does, and reactivation removes it)."""
    await redis.set(suspended_key(str(company_id)), "1")


async def clear_suspended(redis, company_id) -> None:
    await redis.delete(suspended_key(str(company_id)))
