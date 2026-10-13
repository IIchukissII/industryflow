# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether a company is active, as authentication reads it (ADR-0004 dec 2 rev 2, ADR-0033 dec 3).

A suspended (inactive) company's users are refused at authentication: every session-authenticated
request, login, refresh, socket and capability-authenticated request checks the caller's company
here. The registry is the source of truth; this keeps a short per-process cache of it so that the
check is not a database round trip per request. COMPANY_STATUS_CACHE_SECONDS bounds how stale a
replica's view may be; the replica that performs the transition forgets its entry at once, and the
transition itself ends every session of the company (routers/companies.py), so the cache bounds
only how long an already-issued access token is still honoured elsewhere.

The capability planes outside this service do not read the registry. For them the transition also
writes a suspension marker into the shared capability store (``mark_suspended``), which every
verifier and minter of capability handles consults (capability_auth.SUSPENDED_PREFIX, the shared
contract), so no handle for a suspended company resolves or is minted, at once and everywhere.
"""
import time
from collections import OrderedDict
from typing import Optional

from industryflow_contracts import tenancy
from sqlalchemy import text

from capability_auth import suspended_key
from config import get_settings

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


async def is_company_active(company_id) -> bool:
    """True if the company exists and is active; read through a cache of bounded staleness.

    A malformed identifier is not active. A database error propagates: an unknown answer is not a
    yes, and the request fails rather than being admitted.
    """
    key = _canonical(company_id)
    if key is None:
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
