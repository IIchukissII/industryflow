# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Notebook capability minting and resolution (ADR-0015).

A capability is an opaque, high-entropy handle backed by a short-lived entry in a shared store
(ADR-0015 dec 1). The lookup that authorizes a handle is the same one that can revoke it
(deletion), so revocation is immediate and central with expiry only the backstop (dec 2). Each
handle is bound to one tenant, read-only, and one audience — API or SQL — and the two are never
interchangeable (dec 3).

This module is the store-agnostic logic, tested against a fake store. The store is the platform
shared key-value store (Redis) in production; ``RedisCapabilityStore`` is a thin reference
adapter that is NOT integration-tested here. The kernel never receives a database password —
only these handles (dec 5, ADR-0012 dec 2).
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Optional, Protocol

# Audiences (ADR-0015 dec 3). A handle resolves only for the audience it was minted for.
AUDIENCE_API = "api"  # the tenant-scoped data API (ADR-0011 dec 4)
AUDIENCE_SQL = "sql"  # the SQL access proxy (ADR-0015 dec 5)
AUDIENCE_TRACKING = "tracking"  # the experiment-tracking gateway (ADR-0019)

_AUDIENCES = (AUDIENCE_API, AUDIENCE_SQL, AUDIENCE_TRACKING)
# The data planes are read-only (ADR-0011); tracking is read-write *within the tenant namespace*
# the gateway enforces (ADR-0019) — a kernel logs runs and registers models, but never crosses its
# tenant. For tracking, the boundary is the namespace, not read-only-ness.
_READ_ONLY_AUDIENCES = (AUDIENCE_API, AUDIENCE_SQL)

# Store keys are namespaced so capability handles never collide with other store users.
_KEY_PREFIX = "nbcap:"


class CapabilityStore(Protocol):
    """The slice of the shared store the capability logic needs."""

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    def get(self, key: str) -> Optional[str]: ...
    def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class Binding:
    """The facts a handle is bound to (ADR-0015 dec 1). Always read-only (dec 1, ADR-0011)."""

    user: str
    company_id: str
    audience: str
    read_only: bool = True


def _key(handle: str) -> str:
    return f"{_KEY_PREFIX}{handle}"


def mint(store: CapabilityStore, *, user: str, company_id: str, audience: str, ttl_seconds: int) -> str:
    """Mint a capability: generate an opaque handle and record its binding in the store.

    Returns the handle to inject into the environment. The handle carries no authority by
    itself — its authority is the store entry (ADR-0015 dec 1).
    """
    if audience not in _AUDIENCES:
        raise ValueError(f"unknown capability audience: {audience!r}")
    if not user or not company_id:
        raise ValueError("capability requires a user and a tenant")

    handle = secrets.token_urlsafe(32)
    read_only = audience in _READ_ONLY_AUDIENCES
    record = {"user": user, "company_id": company_id, "audience": audience, "read_only": read_only}
    store.set(_key(handle), json.dumps(record), ttl_seconds)
    return handle


def resolve(store: CapabilityStore, handle: str, *, expected_audience: str) -> Optional[Binding]:
    """Resolve a handle to its binding, or None if absent/expired/revoked/wrong-audience.

    None means "deny": the verifier (data API or SQL proxy) refuses the request. A handle minted
    for one audience never resolves for another (ADR-0015 dec 3).
    """
    if not handle:
        return None
    raw = store.get(_key(handle))
    if raw is None:
        return None  # absent, expired, or revoked — the lookup is the auth check (dec 1-2)
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if record.get("audience") != expected_audience:
        return None  # non-interchangeable across planes (dec 3)
    return Binding(
        user=record["user"],
        company_id=record["company_id"],
        audience=record["audience"],
        read_only=bool(record.get("read_only", True)),
    )


def revoke(store: CapabilityStore, handle: str) -> None:
    """Revoke a handle by deleting its entry (ADR-0015 dec 2) — effective on next use."""
    store.delete(_key(handle))


class RedisCapabilityStore:
    """Reference Redis-backed store (ADR-0015 dec 7). NOT integration-tested here.

    Holds only capability handles and their bindings — never a database or object-store secret.
    """

    def __init__(self, client):
        self._client = client

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._client.set(key, value, ex=ttl_seconds)

    def get(self, key: str) -> Optional[str]:
        value = self._client.get(key)
        if value is None:
            return None
        return value.decode() if isinstance(value, (bytes, bytearray)) else value

    def delete(self, key: str) -> None:
        self._client.delete(key)
