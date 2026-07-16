# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Demand-minting of the upload capability (ADR-0030 dec 3, ADR-0015 dec 4 rev 1) — the **pure** rule,
no I/O.

An upload is a person holding a platform session, not a kernel holding a spawn-injected handle. So
the handle it needs is **demand-minted**: minted at the moment it is asked for, consumed immediately,
and never held. ADR-0015 dec 4 rev 1 permits that only under conditions this module exists to keep:

  * **the tenant comes from the verified principal, never from the caller.** This module takes a
    company_id and will not invent, default, or discover one — the caller must have established it
    from a verified session (ADR-0003 dec 1). Everything else here is worthless if that is wrong,
    which is why ``mint`` refuses an absent tenant rather than minting something unbound;
  * **the minter is this plane's designated authority.** The serving side is where the artifact's
    admission is judged at the registration gate, so the authority that will judge the artifact is
    the one that authorises it to arrive. There is one answer to "who may issue this";
  * **the handle is short-lived**, because it authorises one upload rather than a session;
  * **no session secret travels with it.** The handle's authority is its store entry (ADR-0015
    dec 1); the mediator that accepts it resolves an opaque string and learns nothing else. That is
    the point of the whole pattern — the mediator holding the artifact-store credential must never
    be able to verify a session, because the platform's signature is symmetric and a verifier would
    therefore be a forger, for every tenant, permanently.

**Not read-only** (ADR-0015 dec 1 rev 1): this handle authorises writing one artifact. Its boundary
is the tenant namespace its mediator enforces, not read-only-ness — and a writing handle still
reaches exactly one tenant.

The store adapter is the caller's (the shared key-value store in production). This module holds the
rule; ``routers`` holds the wire.
"""
from __future__ import annotations

import json
import secrets
import uuid
from typing import Any, Dict, Tuple

# This plane's audience (ADR-0030 dec 3). It must never be one the spawner can mint: the spawner
# mints what a kernel holds, and a kernel must not be able to reach the upload surface — the
# audience is what holds the two populations to different admission rules.
AUDIENCE_UPLOAD = "upload"

# The store namespace every capability plane shares (ADR-0015 dec 1).
_KEY_PREFIX = "nbcap:"

# One upload, not one session. Long enough to hand the handle over and push an artifact; short
# enough that a leaked handle is a narrow window. Expiry is the backstop; deletion is revocation
# (ADR-0015 dec 2).
DEFAULT_TTL_SECONDS = 600

# High-entropy, opaque: the handle carries no authority by itself (ADR-0015 dec 1).
_HANDLE_BYTES = 32


def store_key(handle: str) -> str:
    return f"{_KEY_PREFIX}{handle}"


def _validated_tenant(company_id: str) -> str:
    """The tenant, or a refusal. UUID-validated for the same reason every other tenant boundary is
    (ADR-0003): the value becomes a namespace, and a namespace that can be anything is not a
    boundary."""
    return str(uuid.UUID(str(company_id)))


def build_record(*, user: str, company_id: str) -> Tuple[str, Dict[str, Any]]:
    """The (handle, bound facts) for one upload capability — the minting decision, without the store.

    Raises ``ValueError`` when there is no verified principal to bind to. A handle bound to nothing
    is not a weaker capability; it is a hole, so this refuses instead of minting.
    """
    if not user:
        raise ValueError("an upload capability requires the verified user it was minted for")
    if not company_id:
        raise ValueError("an upload capability requires exactly one tenant")

    record = {
        "user": user,
        "company_id": _validated_tenant(company_id),
        "audience": AUDIENCE_UPLOAD,
        # ADR-0015 dec 1 rev 1: a bound fact, per handle. This plane writes; its boundary is the
        # tenant namespace the mediator enforces.
        "read_only": False,
    }
    return secrets.token_urlsafe(_HANDLE_BYTES), record


def mint(store_set, *, user: str, company_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Mint an upload capability and record its binding; returns the opaque handle.

    ``store_set(key, value, ttl_seconds)`` is the caller's store adapter.
    """
    if ttl_seconds <= 0:
        raise ValueError("an upload capability must expire")
    handle, record = build_record(user=user, company_id=company_id)
    store_set(store_key(handle), json.dumps(record), ttl_seconds)
    return handle
