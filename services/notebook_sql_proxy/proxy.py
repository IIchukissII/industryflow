# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SQL access proxy orchestration (ADR-0015 dec 5-6).

A notebook kernel connects to this proxy presenting its SQL-audience capability handle in place
of a database password. The proxy resolves the handle to its tenant, opens a backend connection
as that tenant's own confined login, assumes the tenant's read-only role (SET ROLE), and only
then relays the kernel's queries. The kernel never holds a database password (ADR-0012 dec 2).

The handle is not checked once and forgotten: revocation is deletion of its store entry and must
take effect on a session already open (ADR-0012 dec 6, ADR-0015 dec 2), so the relay is handed a
re-check that it runs for as long as the session lives, and ends the session when the handle no
longer resolves to the same binding.

Each presented handle's outcome, and each session's opening and closing with why it ended, are
recorded into the capability-use record (ADR-0042 dec 1); the statements relayed are not.

This module is the connection orchestration, written against a ``Backend`` protocol so the
policy is testable with a fake backend. The real backend — the Postgres wire: the tenant login,
enrolment, the byte relay and cancellation — is ``server.PostgresBackend``.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional, Protocol

from industryflow_contracts import capability_audit as audit

from binding import (SqlBinding, record_resolution, record_session, resolve_sql_outcome,
                     session_setup_statements)

log = logging.getLogger("notebook_sql_proxy")

StillAuthorized = Callable[[], Awaitable[bool]]


class Backend(Protocol):
    """A backend Postgres session the proxy controls on the kernel's behalf."""

    async def connect(self) -> None: ...
    async def execute(self, sql: str) -> None: ...
    async def relay(self, still_authorized: StillAuthorized) -> None: ...
    async def close(self) -> None: ...


class CapabilityStore(Protocol):
    """The capability store (redis.asyncio): the handle lookup, and the record's appends (ADR-0042)."""

    async def get(self, key: str) -> Optional[object]: ...
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...
    async def xadd(self, name: str, fields: dict, **kwargs: object) -> object: ...


async def serve_connection(
    handle: str,
    store: CapabilityStore,
    backend_factory: Callable[[SqlBinding], Backend],
    *,
    source: Optional[str] = None,
) -> bool:
    """Authorize a kernel connection and, if valid, bind it to its tenant and relay.

    Returns True if the connection was authorized and served, False if denied. On denial no
    backend is opened — no database session is opened for an unresolved handle.

    The outcome is recorded before anything is served (ADR-0042 dec 1, 5): the handle's first use
    here, or the refusal. A record that cannot be written refuses the connection by raising, as a
    store that cannot answer the lookup does. The session's opening and closing are recorded too.
    """
    binding, reason, facts = await resolve_sql_outcome(store.get, handle)
    await record_resolution(store, handle, binding, reason, facts, source=source)
    if binding is None:
        # deny: absent/expired/revoked/wrong-audience handle (ADR-0015). Never the handle itself.
        log.info("capability refused: reason=%s user=%s company_id=%s handle_id=%s digest=%s",
                 reason, facts.get("user"), facts.get("company_id"), facts.get("handle_id"),
                 None if facts.get("handle_id") else audit.presented_digest(handle))
        return False

    # Why the session ended, when the re-check ended it rather than either side closing.
    ended: dict = {}

    async def still_authorized() -> bool:
        # The same lookup that granted the session. Revoked, expired, or re-bound to anything
        # else (another tenant, another plane) all end it.
        try:
            current, refusal, _facts = await resolve_sql_outcome(store.get, handle)
        except Exception:
            ended.setdefault("reason", "recheck_failed")
            raise
        if current != binding:
            ended.setdefault("reason", "capability_revoked")
            ended.setdefault("refusal", refusal or "rebound")
            return False
        return True

    backend = backend_factory(binding)
    opened_at: Optional[float] = None
    try:
        await backend.connect()
        for statement in session_setup_statements(binding):
            await backend.execute(statement)
        await record_session(store, audit.SQL_SESSION_OPENED, binding)
        opened_at = time.monotonic()
        await backend.relay(still_authorized)
    except BaseException:
        ended.setdefault("reason", "error")
        raise
    finally:
        await backend.close()
        if opened_at is not None:
            await _record_closed(store, binding, ended, time.monotonic() - opened_at)
    return True


async def _record_closed(store: CapabilityStore, binding: SqlBinding, ended: dict,
                         seconds: float) -> None:
    """Record the session's end. Best effort: the session is over either way, and a failure here
    must not hide the one that ended it."""
    detail = f"duration_seconds={seconds:.1f}"
    if "refusal" in ended:
        detail += f" refusal={ended['refusal']}"
    try:
        await record_session(store, audit.SQL_SESSION_CLOSED, binding,
                             reason=ended.get("reason", "client_closed"), detail=detail)
    except Exception:  # noqa: BLE001 - logged; the close it describes already happened
        log.exception("could not record the SQL session's closing")
