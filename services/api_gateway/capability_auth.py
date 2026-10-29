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

import logging

import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Tuple

from industryflow_contracts import capability_audit as audit

AUDIENCE_API = "api"
_KEY_PREFIX = "nbcap:"
# The hub's per-user index of the handles it minted (handle -> server).
_USER_INDEX_PREFIX = "nbcap-user:"
# The model-upload minter's per-user index of the upload handles it demand-minted (a set).
# Shared contract with services/ml_service/api/upload_capability.py.
_UPLOAD_INDEX_PREFIX = "nbcap-upload-user:"
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
    # The non-secret identifier the minter bound into the entry (ADR-0042 dec 2); None for an entry
    # minted before the record existed.
    handle_id: Optional[str] = None


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
    binding, _reason, _facts = await resolve_capability_outcome(aget, handle, expected_audience=expected_audience)
    return binding


async def resolve_capability_outcome(
    aget: Callable[[str], Awaitable[Optional[object]]],
    handle: str,
    *,
    expected_audience: str = AUDIENCE_API,
) -> Tuple[Optional[CapabilityBinding], Optional[str], Dict[str, str]]:
    """``resolve_capability``, also saying why it denied and what the entry named, for the record
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
    if record.get("audience") != expected_audience:
        return None, "wrong_audience", facts
    try:
        # Canonical, or deny: the tenant scopes every query this request makes (ADR-0003).
        company_id = str(uuid.UUID(str(record.get("company_id") or "")))
    except ValueError:
        return None, "malformed_entry", facts
    if await aget(suspended_key(company_id)) is not None:
        return None, "tenant_suspended", facts
    return CapabilityBinding(
        user=str(record.get("user", "")),
        company_id=company_id,
        audience=record["audience"],
        read_only=bool(record.get("read_only", True)),
        handle_id=facts.get("handle_id"),
    ), None, facts


def _entry_facts(record: dict) -> Dict[str, str]:
    """What an entry says that a record may carry: user, tenant and audience when well-formed, and
    the handle's non-secret identifier."""
    facts: Dict[str, str] = {}
    try:
        facts["company_id"] = str(uuid.UUID(str(record.get("company_id") or "")))
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


async def record_resolution(redis, handle: str, binding: Optional[CapabilityBinding],
                            reason: Optional[str], facts: Dict[str, str], *, source: Optional[str] = None) -> None:
    """Record a presented handle's outcome at this plane (ADR-0042 dec 1, 5): its first use here once,
    and every refusal. An entry minted before the record existed has no identifier and no first use."""
    if binding is not None:
        if binding.handle_id is None:
            return
        fields = audit.event(audit.FIRST_USE, plane="api_gateway", user=binding.user,
                             company_id=binding.company_id, audience=binding.audience,
                             handle_id=binding.handle_id)
        keys, args = audit.first_use_script_args(fields)
        await redis.eval(audit.FIRST_USE_SCRIPT, len(keys), *keys, *args)
        return
    # A value that named no entry is recorded by digest once per window, a real entry's refusal every
    # time and without a digest (ADR-0042 dec 2, 8).
    how, *call = audit.refusal(plane="api_gateway", handle=handle, reason=reason, facts=facts, source=source)
    if how == "once":
        keys, args = call
        if await redis.eval(audit.UNKNOWN_REFUSAL_SCRIPT, len(keys), *keys, *args) == -1:
            logging.getLogger(__name__).warning(
                "capability audit: %s presented more unknown handles than are recorded; the rest of "
                "this minute is not recorded (ADR-0042 dec 9)", source)
    else:
        await redis.xadd(**call[0])


async def revoke_user_capabilities(redis, user_id: str) -> int:
    """Revoke every notebook capability minted for a user — the platform's logout (ADR-0012 dec 6).

    The hub records each handle it mints in a per-user index (services/notebook_hub/capabilities.py,
    the minter's copy of this contract), and the model-upload minter each upload handle in one of its
    own (services/ml_service/api/upload_capability.py). Deleting the handles ends them at every
    verifier at once; the hub notices at its next poll of the user's server and stops it. Returns
    how many were revoked.
    """
    def _s(h):
        return h.decode() if isinstance(h, (bytes, bytearray)) else h

    index = f"{_USER_INDEX_PREFIX}{user_id}"
    upload_index = f"{_UPLOAD_INDEX_PREFIX}{user_id}"
    handles = [_s(h) for h in await redis.hkeys(index)]
    handles += [_s(h) for h in await redis.smembers(upload_index)]
    revoked = 0
    for handle in handles:
        # Each removal is recorded atomically with it, and only if it removed a live entry
        # (ADR-0042 dec 3); the entry is read first for what the record may carry.
        key = f"{_KEY_PREFIX}{handle}"
        raw = await redis.get(key)
        try:
            facts = _entry_facts(json.loads(raw)) if raw is not None else {}
        except (ValueError, TypeError, AttributeError):
            facts = {}
        fields = audit.event(audit.REVOKED, plane="api_gateway", reason="user_signed_out",
                             user=facts.get("user", user_id), company_id=facts.get("company_id"),
                             audience=facts.get("audience"), handle_id=facts.get("handle_id"))
        keys, args = audit.delete_recorded_args(key, fields)
        revoked += int(await redis.eval(audit.DELETE_RECORDED_SCRIPT, len(keys), *keys, *args))
    await redis.delete(index, upload_index)
    return revoked
