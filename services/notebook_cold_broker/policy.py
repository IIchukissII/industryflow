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

CANONICAL-PREFIX CONTRACT: the prefix here is the exporter's write-side prefix plus a trailing ``/``.
Both take it from the one tenant-naming module, ``industryflow_contracts.tenancy.cold_object_prefix``
(``tenant_<canonical-uuid>`` with hyphens): the exporter writes `tenant_0b2f503a-6e59-.../year=…`,
and the broker reads the same bytes. It is HYPHENATED, unlike the underscore schema/role names in the
SQL/tracking planes; the tenancy module documents why the two spellings are distinct.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from industryflow_contracts import tenancy

AUDIENCE_COLD = "cold"  # the broker's copy of the audience (ADR-0025 / ADR-0015 dec 3)
_KEY_PREFIX = "nbcap:"
# Present while the handle's company is suspended (ADR-0033 dec 3): its handles do not resolve.
# Written and removed by the platform API with the company's active flag; shared contract
# (services/api_gateway/capability_auth.py).
_SUSPENDED_PREFIX = "nbcap-suspended:"


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
    is the one that revokes. A handle whose tenant is suspended denies.
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
    try:
        canonical = tenancy.canonical_company_id(company_id)
    except ValueError:
        return None
    if await aget(f"{_SUSPENDED_PREFIX}{canonical}") is not None:
        return None
    return ColdBinding(user=record.get("user", ""), company_id=company_id)


# The tenant's cold-store key prefix, ``tenant_<uuid>/`` (UUID-validated): the exporter's write
# prefix plus the separator, so a sibling ``tenant_<other>`` can never pass a prefix test against it.
tenant_prefix = tenancy.cold_key_prefix


# The ownership test itself is the shared one (ADR-0039); these bind it to the caller's tenant.

def owns_key(company_id: str, key: str) -> bool:
    """Whether an object-store key is a plain key under the caller's tenant prefix — checked before
    the broker signs a pre-signed URL for it, so a tenant can never address another's Parquet."""
    return tenancy.owns_key(tenant_prefix(company_id), key)


def scope_key(company_id: str, path: str) -> str:
    """Place a kernel-relative path under the tenant prefix (idempotent), or ValueError for a path
    that is not plain (ADR-0039 dec 2, 4): an empty, ``.`` or ``..`` segment or a backslash."""
    return tenancy.scope_key(tenant_prefix(company_id), path)
