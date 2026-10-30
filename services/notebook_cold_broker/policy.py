# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant-scoping policy for the cold-store broker (ADR-0025 dec 5, read side).

The broker authenticates a notebook's *cold* capability, resolves it to one tenant, and confines
every object-store key to that tenant's prefix. This module is the policy — the decisions, with
the store reached only through what the caller passes in:

  * resolve a cold-audience handle to its tenant (the audience check + the bound tenant), and
    record the outcome into the capability-use record (ADR-0042);
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

import logging

import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Tuple

from industryflow_contracts import capability_audit as audit
from industryflow_contracts import tenancy

AUDIENCE_COLD = "cold"  # the broker's copy of the audience (ADR-0025 / ADR-0015 dec 3)
# This plane's name in the capability-use record (ADR-0042).
PLANE = "cold_broker"
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
    # The non-secret identifier the minter bound into the entry (ADR-0042 dec 2); None for an entry
    # minted before the record existed.
    handle_id: Optional[str] = None


async def resolve_cold_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Optional[ColdBinding]:
    """Resolve a cold-audience handle to its tenant, or None to deny.

    None means the broker refuses the request. A non-cold handle never resolves here (ADR-0015
    dec 3); absent/expired/revoked/malformed all deny (dec 1-2) — the same lookup that authorizes
    is the one that revokes. A record that is not an object, or whose tenant is missing or not a
    UUID, denies rather than raising. A handle whose tenant is suspended denies.
    """
    binding, _reason, _facts = await resolve_cold_outcome(aget, handle)
    return binding


async def resolve_cold_outcome(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Tuple[Optional[ColdBinding], Optional[str], Dict[str, str]]:
    """``resolve_cold_binding``, also saying why it denied and what the entry named, for the record
    (ADR-0042 dec 1): ``(binding, refusal reason, entry facts)``; the binding is None exactly when a
    reason is given. Facts are what a record may carry, never the handle."""
    if not handle:
        return None, "no_handle", {}
    raw = await aget(f"{_KEY_PREFIX}{handle}")
    if raw is None:
        return None, "no_entry", {}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return None, "malformed_entry", {}
    if not isinstance(record, dict):
        return None, "malformed_entry", {}
    facts = _entry_facts(record)
    if record.get("audience") != AUDIENCE_COLD:
        return None, "wrong_audience", facts
    try:
        # Canonical, or deny: the tenant becomes the object-store prefix every key is confined to.
        company_id = tenancy.canonical_company_id(record.get("company_id") or "")
    except ValueError:
        return None, "malformed_entry", facts
    if await aget(f"{_SUSPENDED_PREFIX}{company_id}") is not None:
        return None, "tenant_suspended", facts
    return ColdBinding(user=str(record.get("user", "")), company_id=company_id,
                       handle_id=facts.get("handle_id")), None, facts


def _entry_facts(record: dict) -> Dict[str, str]:
    """What an entry says that a record may carry: user, tenant and audience when well-formed, and
    the handle's non-secret identifier."""
    facts: Dict[str, str] = {}
    try:
        facts["company_id"] = tenancy.canonical_company_id(record.get("company_id") or "")
    except ValueError:
        pass
    try:
        facts["handle_id"] = str(uuid.UUID(str(record.get("handle_id") or "")))
    except ValueError:
        pass
    if isinstance(record.get("user"), str):
        facts["user"] = record["user"]
    if record.get("audience") in audit.AUDIENCES:
        facts["audience"] = record["audience"]
    return facts


async def record_resolution(store, handle: str, binding: Optional[ColdBinding],
                            reason: Optional[str], facts: Dict[str, str], *, source: Optional[str] = None) -> None:
    """Record a presented handle's outcome at the broker (ADR-0042 dec 1, 5): its first use here
    once, and its refusals (dec 8). An entry minted before the record existed has no identifier and no
    first use. ``store`` is the capability store (redis.asyncio)."""
    if binding is not None:
        if binding.handle_id is None:
            return
        fields = audit.event(audit.FIRST_USE, plane=PLANE, user=binding.user,
                             company_id=binding.company_id, audience=AUDIENCE_COLD,
                             handle_id=binding.handle_id)
        keys, args = audit.first_use_script_args(fields)
        await store.eval(audit.FIRST_USE_SCRIPT, len(keys), *keys, *args)
        return
    # A value that named no entry is recorded by digest once per window, a real entry's refusal once per
    # window with the count of the rest, and without a digest (ADR-0042 dec 2, 8).
    script, keys, args = audit.refusal(plane=PLANE, handle=handle, reason=reason, facts=facts, source=source)
    if await store.eval(script, len(keys), *keys, *args) == -1:
        logging.getLogger(__name__).warning(
            "capability audit: %s presented more unknown handles than are recorded; the rest of "
            "this minute is not recorded (ADR-0042 dec 9)", source)


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
