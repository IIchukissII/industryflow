# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant-scoping policy for the cold-store broker (ADR-0025 dec 5, read side).

The broker authenticates a notebook's *cold* capability, resolves it to one tenant, and confines
every object-store key to that tenant's prefix. This module is the **pure** policy — the
decisions, no I/O:

  * resolve a cold-audience handle to its tenant (the audience check + the bound tenant);
  * the tenant's object-store prefix, and the predicates "force this path under my prefix" /
    "does this key belong to me".

The live boto3 pre-signing + listing and the FastAPI wiring live in ``broker.py``; this layer is
fully unit-testable on dicts and strings.

CANONICAL-PREFIX CONTRACT: the prefix here MUST equal the exporter's write-side mapping,
``services/cold_export/naming.company_id_to_prefix`` (``tenant_<canonical-uuid>`` with hyphens),
plus a trailing ``/``. The exporter writes `tenant_0b2f503a-6e59-.../year=…`; the broker must read
the same bytes. This is the object-store analogue of ADR-0003's single ``company_id → schema``
mapping — note it is HYPHENATED, unlike the underscore schema/role names in the SQL/tracking planes.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

AUDIENCE_COLD = "cold"  # the broker's copy of the audience (ADR-0025 / ADR-0015 dec 3)
_KEY_PREFIX = "nbcap:"


@dataclass(frozen=True)
class ColdBinding:
    """The tenant a cold handle is bound to (ADR-0025). Read-only by construction."""

    user: str
    company_id: str


async def resolve_cold_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Optional[ColdBinding]:
    """Resolve a cold-audience handle to its tenant, or None to deny.

    None means the broker refuses the request. A non-cold handle never resolves here (ADR-0015
    dec 3); absent/expired/revoked/malformed all deny (dec 1-2) — the same lookup that authorizes
    is the one that revokes.
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
    if record.get("audience") != AUDIENCE_COLD:
        return None
    company_id = record.get("company_id")
    if not company_id:
        return None
    return ColdBinding(user=record.get("user", ""), company_id=company_id)


def tenant_prefix(company_id: str) -> str:
    """The tenant's cold-store key prefix, e.g. ``tenant_<uuid>/`` (UUID-validated).

    Must match the exporter's write path (services/cold_export/naming.company_id_to_prefix) — a
    hyphenated canonical UUID — or the broker would read an empty prefix.
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical}/"


def owns_key(company_id: str, key: str) -> bool:
    """Whether an object-store key is under the caller's tenant prefix — checked before the broker
    signs a pre-signed URL for it, so a tenant can never address another's Parquet."""
    key = (key or "").lstrip("/")
    return bool(key) and key.startswith(tenant_prefix(company_id))


def scope_key(company_id: str, path: str) -> str:
    """Place a kernel-relative path under the tenant prefix (idempotent).

    A path already inside the tenant prefix is returned unchanged; anything else is prepended.
    Because the tenant prefix ends in ``/`` and is derived from a validated UUID, a sibling prefix
    (``tenant_<other>``) can never be mistaken for the caller's, and ``..`` segments are rejected
    so a crafted path cannot walk out of the prefix.
    """
    path = (path or "").lstrip("/")
    if ".." in path.split("/"):
        raise ValueError("path may not contain '..' segments")
    prefix = tenant_prefix(company_id)
    return path if path.startswith(prefix) else f"{prefix}{path}"
