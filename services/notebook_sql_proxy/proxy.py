# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SQL access proxy orchestration (ADR-0015 dec 5-6).

A notebook kernel connects to this proxy presenting its SQL-audience capability handle in place
of a database password. The proxy resolves the handle to its tenant, opens a backend connection
as the privileged principal, assumes that tenant's read-only role (SET ROLE), and only then
relays the kernel's queries. The kernel never holds a database password (ADR-0012 dec 2).

This module is the connection orchestration, written against a ``Backend`` protocol so the
policy is testable with a fake backend. The real backend — the Postgres wire: reading the
handle from the client's startup/password message, holding the privileged credential, and
relaying bytes — is the cluster-bound adapter (see README) and is NOT implemented here.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional, Protocol

from binding import resolve_sql_binding, session_setup_statements


class Backend(Protocol):
    """A backend Postgres session the proxy controls on the kernel's behalf."""

    async def connect_privileged(self) -> None: ...
    async def execute(self, sql: str) -> None: ...
    async def relay(self) -> None: ...
    async def close(self) -> None: ...


async def serve_connection(
    handle: str,
    store_get: Callable[[str], Awaitable[Optional[object]]],
    backend_factory: Callable[["SqlBinding"], Backend],  # noqa: F821 - forward type hint
) -> bool:
    """Authorize a kernel connection and, if valid, bind it to its tenant and relay.

    Returns True if the connection was authorized and served, False if denied. On denial no
    backend is opened — the privileged principal is never used for an unresolved handle.
    """
    binding = await resolve_sql_binding(store_get, handle)
    if binding is None:
        return False  # deny: absent/expired/revoked/wrong-audience handle (ADR-0015)

    backend = backend_factory(binding)
    try:
        await backend.connect_privileged()
        for statement in session_setup_statements(binding):
            await backend.execute(statement)
        await backend.relay()
    finally:
        await backend.close()
    return True
