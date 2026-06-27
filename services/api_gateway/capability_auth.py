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
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

AUDIENCE_API = "api"
_KEY_PREFIX = "nbcap:"


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
    or None. None / unknown / wrong-audience / malformed all deny.
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
    if record.get("audience") != expected_audience:
        return None
    return CapabilityBinding(
        user=record.get("user", ""),
        company_id=record["company_id"],
        audience=record["audience"],
        read_only=bool(record.get("read_only", True)),
    )
