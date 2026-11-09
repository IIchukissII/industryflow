# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The denials the shared capability store holds, and whether it can be trusted for them (ADR-0015 dec 8).

A tenant's suspension marker (ADR-0033 dec 3) and the records of ended sessions (``sessions``) are
denials whose absence grants. A store that was lost and recreated empty holds none of them, and
cannot be told from one that holds none because nothing was denied. So the store is trusted only once
it carries ``STORE_REBUILT_KEY``: the API gateway writes it, last, after re-deriving every denial it
can (each inactive company's marker, from the registry), and its value, the rebuild time, ends every
session issued before it. Until it is there, every verifier and minter refuses.

Standard library only: this is imported by every service image.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

# Present while a company is suspended; nothing of it is minted or honoured.
SUSPENDED_PREFIX = "nbcap-suspended:"
# When the store was last rebuilt (epoch seconds); absent means it cannot be trusted.
STORE_REBUILT_KEY = "capability-store-rebuilt"

# The refusal reasons a verifier records (ADR-0042 dec 8).
STORE_NOT_REBUILT = "store_not_rebuilt"
TENANT_SUSPENDED = "tenant_suspended"


class StoreNotRebuilt(RuntimeError):
    """The capability store carries no rebuild record, so the denials in it are unknown."""


def suspended_key(company_id) -> str:
    return f"{SUSPENDED_PREFIX}{company_id}"


def _text(value) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


def rebuilt_at(raw) -> float:
    """The rebuild time a read of ``STORE_REBUILT_KEY`` returned, or ``StoreNotRebuilt``."""
    if raw is None:
        raise StoreNotRebuilt("the capability store has not been rebuilt")
    try:
        return float(_text(raw))
    except ValueError:
        raise StoreNotRebuilt("the capability store's rebuild record is unreadable") from None


def _rebuilt(raw) -> bool:
    try:
        rebuilt_at(raw)
    except StoreNotRebuilt:
        return False
    return True


async def tenant_refusal(aget: Callable[[str], Awaitable[Optional[object]]], company_id) -> Optional[str]:
    """Why nothing of the company may be minted or honoured now, or None.

    ``aget`` reads one key of the store. A store not rebuilt, or whose record is unreadable, refuses
    every tenant."""
    if not _rebuilt(await aget(STORE_REBUILT_KEY)):
        return STORE_NOT_REBUILT
    if await aget(suspended_key(company_id)) is not None:
        return TENANT_SUSPENDED
    return None


def tenant_refusal_sync(get: Callable[[str], Optional[object]], company_id) -> Optional[str]:
    """``tenant_refusal`` for a synchronous store client."""
    if not _rebuilt(get(STORE_REBUILT_KEY)):
        return STORE_NOT_REBUILT
    if get(suspended_key(company_id)) is not None:
        return TENANT_SUSPENDED
    return None
