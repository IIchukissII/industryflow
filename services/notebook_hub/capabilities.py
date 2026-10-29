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
read-only, the tracking plane read-write within its tenant namespace (dec 1).

Lifecycle (dec 2, ADR-0012 dec 6). The hub records every handle it mints for a server in a
per-user index in the same store, so that it can:

  * **refresh** them while the server is healthy (``refresh_server`` — the entry's lifetime is then
    a backstop measured in minutes, not the length of a session);
  * **revoke** them when the server stops or is replaced by a respawn (``revoke_server``);
  * let the platform **revoke all of a user's handles at logout** (``revoke_user``; the data API's
    copy of that rule is services/api_gateway/capability_auth.py).

Record (ADR-0042). Every entry carries a non-secret ``handle_id`` bound at mint (dec 2). A mint and
its ``minted`` event are one transaction, and a revocation is recorded by the contract's
delete-and-record script only when it removed a live entry (dec 3); the start and stop of the
environment the handles were minted for are recorded too (dec 1). The handle itself never enters an
event.

This module is the store-agnostic logic, tested against a fake store. The store is the platform
shared key-value store (Redis) in production; ``RedisCapabilityStore`` is a thin adapter. The
kernel never receives a database password — only these handles (dec 5, ADR-0012 dec 2).
"""
from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Protocol, Tuple

from industryflow_contracts import capability_audit as audit

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
# Present while a company is suspended (ADR-0033 dec 3): nothing is minted for it and none of its
# handles resolve. Written and removed by the platform API with the company's active flag; shared
# contract with every verifier (services/api_gateway/capability_auth.py).
_SUSPENDED_PREFIX = "nbcap-suspended:"

# The plane the hub records as (ADR-0042 dec 1).
PLANE = "hub"
# Why a handle was revoked, or an environment stopped (ADR-0042 dec 1).
REASON_SERVER_STOPPED = "server_stopped"
REASON_RESPAWN = "respawn"
REASON_USER_SIGNED_OUT = "user_signed_out"
REASON_IDENTITY_CHANGED = "identity_changed"
# The handles were revoked elsewhere (the platform's logout), so the hub stopped the server.
REASON_CAPABILITIES_REVOKED = "capabilities_revoked"
REASON_REVOKED = "revoked"


class CapabilityStore(Protocol):
    """The slice of the shared store the capability logic needs."""

    def get(self, key: str) -> Optional[str]: ...
    def delete(self, *keys: str) -> None: ...
    def expire(self, key: str, ttl_seconds: int) -> bool: ...   # False if the key does not exist
    def index_items(self, key: str) -> Dict[str, str]: ...
    def index_remove(self, key: str, *fields: str) -> None: ...
    # The only write of an entry. One transaction: the entry, its index field (``(key, field,
    # value)``, if given) and ``event`` appended to the record's stream (ADR-0042 dec 3).
    def set_recorded(self, key: str, value: str, ttl_seconds: int, event: Mapping[str, str],
                     index: Optional[Tuple[str, str, str]] = None) -> None: ...
    # Runs one of the record contract's scripts (``capability_audit``); returns its integer reply.
    def eval(self, script: str, keys: List[str], args: List[str]) -> int: ...
    # A plain bounded append of ``event`` to the record's stream.
    def append(self, event: Mapping[str, str]) -> None: ...


@dataclass(frozen=True)
class Binding:
    """The facts a handle is bound to (ADR-0015 dec 1): one user, one tenant, one audience, and
    whether it may write (read-only for every plane but tracking, dec 1)."""

    user: str
    company_id: str
    audience: str
    read_only: bool = True


class CompanySuspended(ValueError):
    """The tenant is suspended, so no capability is minted for it (ADR-0033 dec 3)."""


def is_suspended(store: CapabilityStore, company_id: str) -> bool:
    return store.get(f"{_SUSPENDED_PREFIX}{company_id}") is not None


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
    if is_suspended(store, company_id):
        raise CompanySuspended(f"tenant {company_id} is suspended")

    handle = secrets.token_urlsafe(32)
    read_only = audience in _READ_ONLY_AUDIENCES
    # The record's name for this handle (ADR-0042 dec 2): bound into the entry, never derived from it.
    handle_id = audit.new_handle_id()
    record = {"user": user, "company_id": company_id, "audience": audience, "read_only": read_only,
              "handle_id": handle_id}
    # Built before anything is written: a tenant the record cannot name is refused here (ValueError),
    # not minted unrecorded.
    event = audit.event(
        audit.MINTED, plane=PLANE, user=user, company_id=company_id, audience=audience,
        handle_id=handle_id, detail=None if server_name is None else f"server={server_name}",
    )
    # The entry, its index field and its record in one transaction (ADR-0042 dec 3): a handle that is
    # not indexed is one no lifecycle event can revoke, and one unrecorded is one no record shows.
    index = (_user_index(user), handle, server_name) if server_name is not None else None
    store.set_recorded(_key(handle), json.dumps(record), ttl_seconds, event, index=index)
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
    if is_suspended(store, str(record["company_id"])):
        return None
    return Binding(
        user=str(record["user"]),
        company_id=str(record["company_id"]),
        audience=record["audience"],
        read_only=bool(record.get("read_only", True)),
    )


def revoke(store: CapabilityStore, handle: str, *, reason: str = REASON_REVOKED) -> int:
    """Revoke a handle by deleting its entry (ADR-0015 dec 2) — effective on next use.

    Recorded atomically with the deletion, and only if it removed a live entry (ADR-0042 dec 3).
    Returns how many entries were removed (0 or 1).
    """
    return _revoke_recorded(store, handle, reason)


def _entry_facts(raw: Optional[str]) -> Dict[str, str]:
    """What an entry says that a record may carry: user, tenant and audience when well-formed, and
    the handle's non-secret identifier — absent from an entry minted before the record existed."""
    try:
        record = json.loads(raw) if raw is not None else None
    except (ValueError, TypeError):
        return {}
    if not isinstance(record, dict):
        return {}
    facts: Dict[str, str] = {}
    for name in ("company_id", "handle_id"):
        try:
            facts[name] = str(uuid.UUID(str(record.get(name) or "")))
        except ValueError:
            pass
    if isinstance(record.get("user"), str) and record["user"]:
        facts["user"] = record["user"]
    if record.get("audience") in audit.AUDIENCES:
        facts["audience"] = record["audience"]
    return facts


def _revoke_recorded(store: CapabilityStore, handle: str, reason: str,
                     user: Optional[str] = None) -> int:
    # The entry is read first for what the record may carry; the deletion and its record are then
    # one script, so an entry that expired in between is recorded as nothing (ADR-0042 dec 3).
    key = _key(handle)
    facts = _entry_facts(store.get(key))
    fields = audit.event(
        audit.REVOKED, plane=PLANE, reason=reason, user=facts.get("user", user),
        company_id=facts.get("company_id"), audience=facts.get("audience"),
        handle_id=facts.get("handle_id"),
    )
    keys, args = audit.delete_recorded_args(key, fields)
    return int(store.eval(audit.DELETE_RECORDED_SCRIPT, keys, args))


def _server_handles(store: CapabilityStore, user: str, server_name: str) -> list:
    return [h for h, s in store.index_items(_user_index(user)).items() if s == server_name]


def revoke_server(store: CapabilityStore, user: str, server_name: str, *,
                  reason: str = REASON_SERVER_STOPPED) -> int:
    """Revoke every handle minted for one of a user's servers. Returns how many were indexed.

    Called when the server stops and before a respawn mints its replacements: a handle outlives
    the environment it was minted for only until this runs, never until it expires. Each live one
    is recorded as revoked for ``reason`` (ADR-0042 dec 1).
    """
    handles = _server_handles(store, user, server_name)
    for handle in handles:
        _revoke_recorded(store, handle, reason, user)
    if handles:
        store.index_remove(_user_index(user), *handles)
    return len(handles)


def revoke_user(store: CapabilityStore, user: str, *, reason: str = REASON_USER_SIGNED_OUT) -> int:
    """Revoke every handle minted for any of a user's servers (platform logout)."""
    index = _user_index(user)
    handles = list(store.index_items(index))
    for handle in handles:
        _revoke_recorded(store, handle, reason, user)
    store.delete(index)
    return len(handles)


def _tenant_or_none(company_id) -> Optional[str]:
    try:
        return str(uuid.UUID(str(company_id or "")))
    except ValueError:
        return None


def record_environment_started(store: CapabilityStore, *, user: str, company_id: str,
                               server_name: str, profile: Optional[str] = None) -> None:
    """Record that an environment was spawned for a user in a tenant (ADR-0042 dec 1)."""
    detail = f"server={server_name}" + (f" profile={profile}" if profile else "")
    store.append(audit.event(audit.ENVIRONMENT_STARTED, plane=PLANE, user=user,
                             company_id=_tenant_or_none(company_id), detail=detail))


def record_environment_stopped(store: CapabilityStore, *, user: str, company_id: Optional[str],
                               server_name: str, reason: str) -> None:
    """Record that a user's environment stopped, and why (ADR-0042 dec 1)."""
    store.append(audit.event(audit.ENVIRONMENT_STOPPED, plane=PLANE, user=user,
                             company_id=_tenant_or_none(company_id), reason=reason,
                             detail=f"server={server_name}"))


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


class RedisCapabilityStore:
    """Redis-backed store (ADR-0015 dec 7).

    Holds only capability handles, their bindings and the per-user index of them — never a database
    or object-store secret — and the capability-use record's stream (ADR-0042 dec 3).
    """

    def __init__(self, client):
        self._client = client

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

    def index_items(self, key: str) -> Dict[str, str]:
        raw = self._client.hgetall(key) or {}

        def s(v):
            return v.decode() if isinstance(v, (bytes, bytearray)) else v

        return {s(k): s(v) for k, v in raw.items()}

    def index_remove(self, key: str, *fields: str) -> None:
        if fields:
            self._client.hdel(key, *fields)

    def set_recorded(self, key: str, value: str, ttl_seconds: int, event: Mapping[str, str],
                     index: Optional[Tuple[str, str, str]] = None) -> None:
        # MULTI/EXEC: the entry, its index and its record land together or not at all.
        pipe = self._client.pipeline(transaction=True)
        if index is not None:
            index_key, field, index_value = index
            pipe.hset(index_key, field, index_value)
            pipe.expire(index_key, ttl_seconds)
        pipe.set(key, value, ex=ttl_seconds)
        pipe.xadd(**audit.append_args(event))
        pipe.execute()

    def eval(self, script: str, keys: List[str], args: List[str]) -> int:
        return int(self._client.eval(script, len(keys), *keys, *args))

    def append(self, event: Mapping[str, str]) -> None:
        self._client.xadd(**audit.append_args(event))
