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

This module is the connection orchestration, written against a ``Backend`` protocol so the
policy is testable with a fake backend. The real backend — the Postgres wire: the tenant login,
enrolment, the byte relay and cancellation — is ``server.PostgresBackend``.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol

from binding import SqlBinding, resolve_sql_binding, session_setup_statements

StillAuthorized = Callable[[], Awaitable[bool]]


class Backend(Protocol):
    """A backend Postgres session the proxy controls on the kernel's behalf."""

    async def connect(self) -> None: ...
    async def execute(self, sql: str) -> None: ...
    async def relay(self, still_authorized: StillAuthorized) -> None: ...
    async def close(self) -> None: ...


async def serve_connection(
    handle: str,
    store_get: Callable[[str], Awaitable[Optional[object]]],
    backend_factory: Callable[[SqlBinding], Backend],
) -> bool:
    """Authorize a kernel connection and, if valid, bind it to its tenant and relay.

    Returns True if the connection was authorized and served, False if denied. On denial no
    backend is opened — no database session is opened for an unresolved handle.
    """
    binding = await resolve_sql_binding(store_get, handle)
    if binding is None:
        return False  # deny: absent/expired/revoked/wrong-audience handle (ADR-0015)

    async def still_authorized() -> bool:
        # The same lookup that granted the session. Revoked, expired, or re-bound to anything
        # else (another tenant, another plane) all end it.
        return await resolve_sql_binding(store_get, handle) == binding

    backend = backend_factory(binding)
    try:
        await backend.connect()
        for statement in session_setup_statements(binding):
            await backend.execute(statement)
        await backend.relay(still_authorized)
    finally:
        await backend.close()
    return True
