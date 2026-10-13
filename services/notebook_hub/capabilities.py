# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Notebook capability minting, resolution and lifecycle (ADR-0015).

A capability is an opaque, high-entropy handle backed by a short-lived entry in a shared store
(ADR-0015 dec 1). The lookup that authorizes a handle is the same one that can revoke it
(deletion), so revocation is immediate and central with expiry only the backstop (dec 2). Each
handle is bound to one tenant and one audience, and audiences are never interchangeable (dec 3).
Read-only is a bound fact per handle, not a property of every handle: the data planes are minted
read-only, the tracking plane read-write within its tenant namespace (dec 1 rev 1).

Lifecycle (dec 2, ADR-0012 dec 6). The hub records every handle it mints for a server in a
per-user index in the same store, so that it can:

  * **refresh** them while the server is healthy (``refresh_server`` — the entry's lifetime is then
    a backstop measured in minutes, not the length of a session);
  * **revoke** them when the server stops or is replaced by a respawn (``revoke_server``);
  * let the platform **revoke all of a user's handles at logout** (``revoke_user``; the data API's
    copy of that rule is services/api_gateway/capability_auth.py).

This module is the store-agnostic logic, tested against a fake store. The store is the platform
shared key-value store (Redis) in production; ``RedisCapabilityStore`` is a thin adapter. The
kernel never receives a database password — only these handles (dec 5, ADR-0012 dec 2).
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Protocol

# Audiences (ADR-0015 dec 3). A handle resolves only for the audience it was minted for.
AUDIENCE_API = "api"  # the tenant-scoped data API (ADR-0011 dec 4)
AUDIENCE_SQL = "sql"  # the SQL access proxy (ADR-0015 dec 5)
AUDIENCE_TRACKING = "tracking"  # the experiment-tracking gateway (ADR-0019)
AUDIENCE_COLD = "cold"  # the cold-store broker (ADR-0025 dec 5, read side)

_AUDIENCES = (AUDIENCE_API, AUDIENCE_SQL, AUDIENCE_TRACKING, AUDIENCE_COLD)
# The data planes are read-only (ADR-0011); tracking is read-write *within the tenant namespace*
# the gateway enforces (ADR-0019) — a kernel logs runs and registers models, but never crosses its
# tenant. For tracking, the boundary is the namespace, not read-only-ness. The cold plane is pure
# read: training reads long-horizon history and writes nothing (ADR-0025).
_READ_ONLY_AUDIENCES = (AUDIENCE_API, AUDIENCE_SQL, AUDIENCE_COLD)

# Store keys are namespaced so capability handles never collide with other store users.
_KEY_PREFIX = "nbcap:"
# Per-user index of minted handles: handle -> the server it was minted for. Shared contract with
# the data API, which revokes a user's handles at platform logout.
_USER_INDEX_PREFIX = "nbcap-user:"


class CapabilityStore(Protocol):
    """The slice of the shared store the capability logic needs."""

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...
    def get(self, key: str) -> Optional[str]: ...
    def delete(self, *keys: str) -> None: ...
    def expire(self, key: str, ttl_seconds: int) -> bool: ...   # False if the key does not exist
    def index_set(self, key: str, field: str, value: str, ttl_seconds: int) -> None: ...
    def index_items(self, key: str) -> Dict[str, str]: ...
    def index_remove(self, key: str, *fields: str) -> None: ...


@dataclass(frozen=True)
class Binding:
    """The facts a handle is bound to (ADR-0015 dec 1): one user, one tenant, one audience, and
    whether it may write (read-only for every plane but tracking, dec 1 rev 1)."""

    user: str
    company_id: str
    audience: str
    read_only: bool = True


def _key(handle: str) -> str:
    return f"{_KEY_PREFIX}{handle}"


def _user_index(user: str) -> str:
    return f"{_USER_INDEX_PREFIX}{user}"


def mint(
    store: CapabilityStore,
    *,
    user: str,
    company_id: str,
    audience: str,
    ttl_seconds: int,
    server_name: Optional[str] = None,
) -> str:
    """Mint a capability: generate an opaque handle and record its binding in the store.

    Returns the handle to inject into the environment. The handle carries no authority by
    itself — its authority is the store entry (ADR-0015 dec 1). With ``server_name`` the handle is
    also recorded against that server, so the server's lifecycle can refresh and revoke it.
    """
    if audience not in _AUDIENCES:
        raise ValueError(f"unknown capability audience: {audience!r}")
    if not user or not company_id:
        raise ValueError("capability requires a user and a tenant")

    handle = secrets.token_urlsafe(32)
    read_only = audience in _READ_ONLY_AUDIENCES
    record = {"user": user, "company_id": company_id, "audience": audience, "read_only": read_only}
    # Index first: a handle that exists but is not indexed is one no lifecycle event can revoke.
    if server_name is not None:
        store.index_set(_user_index(user), handle, server_name, ttl_seconds)
    store.set(_key(handle), json.dumps(record), ttl_seconds)
    return handle


def resolve(store: CapabilityStore, handle: str, *, expected_audience: str) -> Optional[Binding]:
    """Resolve a handle to its binding, or None if absent/expired/revoked/wrong-audience/malformed.

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
    if not isinstance(record, dict):
        return None
    if record.get("audience") != expected_audience:
        return None  # non-interchangeable across planes (dec 3)
    if not record.get("company_id") or not record.get("user"):
        return None  # a handle bound to no tenant or no user is a hole, not a capability
    return Binding(
        user=str(record["user"]),
        company_id=str(record["company_id"]),
        audience=record["audience"],
        read_only=bool(record.get("read_only", True)),
    )


def revoke(store: CapabilityStore, handle: str) -> None:
    """Revoke a handle by deleting its entry (ADR-0015 dec 2) — effective on next use."""
    store.delete(_key(handle))


def _server_handles(store: CapabilityStore, user: str, server_name: str) -> list:
    return [h for h, s in store.index_items(_user_index(user)).items() if s == server_name]


def revoke_server(store: CapabilityStore, user: str, server_name: str) -> int:
    """Revoke every handle minted for one of a user's servers. Returns how many were indexed.

    Called when the server stops and before a respawn mints its replacements: a handle outlives
    the environment it was minted for only until this runs, never until it expires.
    """
    handles = _server_handles(store, user, server_name)
    _delete_handles(store, handles)
    if handles:
        store.index_remove(_user_index(user), *handles)
    return len(handles)


def revoke_user(store: CapabilityStore, user: str) -> int:
    """Revoke every handle minted for any of a user's servers (platform logout)."""
    index = _user_index(user)
    handles = list(store.index_items(index))
    _delete_handles(store, handles)
    store.delete(index)
    return len(handles)


def refresh_server(store: CapabilityStore, user: str, server_name: str, ttl_seconds: int) -> int:
    """Extend the lifetime of a healthy server's handles. Returns how many are still live.

    Only an existing entry is extended — a revoked handle is never brought back — and handles that
    are gone are dropped from the index. Zero live handles for a server that had some means its
    capabilities were revoked from elsewhere (a platform logout): the caller stops the server.
    """
    index = _user_index(user)
    handles = _server_handles(store, user, server_name)
    live, dead = [], []
    for handle in handles:
        (live if store.expire(_key(handle), ttl_seconds) else dead).append(handle)
    if dead:
        store.index_remove(index, *dead)
    if live:
        store.expire(index, ttl_seconds)
    return len(live)


def _delete_handles(store: CapabilityStore, handles: Iterable[str]) -> None:
    keys = [_key(h) for h in handles]
    if keys:
        store.delete(*keys)


class RedisCapabilityStore:
    """Redis-backed store (ADR-0015 dec 7).

    Holds only capability handles, their bindings and the per-user index of them — never a database
    or object-store secret.
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

    def delete(self, *keys: str) -> None:
        if keys:
            self._client.delete(*keys)

    def expire(self, key: str, ttl_seconds: int) -> bool:
        return bool(self._client.expire(key, ttl_seconds))

    def index_set(self, key: str, field: str, value: str, ttl_seconds: int) -> None:
        pipe = self._client.pipeline()
        pipe.hset(key, field, value)
        pipe.expire(key, ttl_seconds)
        pipe.execute()

    def index_items(self, key: str) -> Dict[str, str]:
        raw = self._client.hgetall(key) or {}

        def s(v):
            return v.decode() if isinstance(v, (bytes, bytearray)) else v

        return {s(k): s(v) for k, v in raw.items()}

    def index_remove(self, key: str, *fields: str) -> None:
        if fields:
            self._client.hdel(key, *fields)
