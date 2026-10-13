# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Verifier-side capability resolution for the data API (ADR-0015).

A notebook kernel reaches the data API with an opaque API-audience capability handle minted by
the hub spawner; this resolves the handle against the shared store (Redis) to its tenant
binding. Resolution returning None means "deny" — the lookup is the auth check, and that same
lookup is what revocation (entry deletion) defeats (ADR-0015 dec 1-2).

The store key format and record shape are the contract shared with the minter
(services/notebook_hub/capabilities.py); this is the verifier's copy of that contract. The
audience check makes an SQL-audience handle unusable here (dec 3).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

AUDIENCE_API = "api"
_KEY_PREFIX = "nbcap:"
# The hub's per-user index of the handles it minted (handle -> server).
_USER_INDEX_PREFIX = "nbcap-user:"
# Present while a company is suspended (ADR-0033 dec 3): no handle bound to it resolves or is
# minted. Written by the gateway when a company is deactivated, removed when it is reactivated
# (company_status.py). Shared contract with every other verifier and minter of handles.
SUSPENDED_PREFIX = "nbcap-suspended:"


def suspended_key(company_id: str) -> str:
    return f"{SUSPENDED_PREFIX}{company_id}"


@dataclass(frozen=True)
class CapabilityBinding:
    user: str
    company_id: str
    audience: str
    read_only: bool = True


async def resolve_capability(
    aget: Callable[[str], Awaitable[Optional[object]]],
    handle: str,
    *,
    expected_audience: str = AUDIENCE_API,
) -> Optional[CapabilityBinding]:
    """Resolve a handle to its binding, or None to deny.

    ``aget`` is an async getter over the store (e.g. ``redis.get``) returning the stored value
    or None. None / unknown / wrong-audience / malformed all deny — including a record that is not
    an object or whose tenant is missing or not a UUID, which must be a refusal, not a 500 — and so
    does a handle whose tenant is suspended.
    """
    if not handle:
        return None
    raw = await aget(f"{_KEY_PREFIX}{handle}")
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("audience") != expected_audience:
        return None
    try:
        # Canonical, or deny: the tenant scopes every query this request makes (ADR-0003).
        company_id = str(uuid.UUID(str(record.get("company_id") or "")))
    except ValueError:
        return None
    if await aget(suspended_key(company_id)) is not None:
        return None
    return CapabilityBinding(
        user=str(record.get("user", "")),
        company_id=company_id,
        audience=record["audience"],
        read_only=bool(record.get("read_only", True)),
    )


async def revoke_user_capabilities(redis, user_id: str) -> int:
    """Revoke every notebook capability minted for a user — the platform's logout (ADR-0012 dec 6).

    The hub records each handle it mints in a per-user index (services/notebook_hub/capabilities.py,
    the minter's copy of this contract). Deleting the handles ends them at every verifier at once;
    the hub notices at its next poll of the user's server and stops it. Returns how many were revoked.
    """
    index = f"{_USER_INDEX_PREFIX}{user_id}"
    handles = await redis.hkeys(index)
    keys = [f"{_KEY_PREFIX}{h.decode() if isinstance(h, (bytes, bytearray)) else h}" for h in handles]
    await redis.delete(index, *keys)
    return len(keys)
