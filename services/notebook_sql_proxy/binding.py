# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SQL proxy binding policy (ADR-0015 dec 5-6).

The proxy resolves an SQL-audience capability handle to its tenant, then assumes that tenant's
read-only role on the backend connection (ADR-0011 keystone). This module is the pure policy:
resolve the handle, derive the per-tenant reader role, and produce the per-connection setup
statements. The proxy itself (orchestration + Postgres wire) lives in proxy.py.

The store key/record shape is the contract shared with the minter
(services/notebook_hub/capabilities.py); this is the SQL verifier's copy.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

AUDIENCE_SQL = "sql"
_KEY_PREFIX = "nbcap:"


@dataclass(frozen=True)
class SqlBinding:
    user: str
    company_id: str
    read_only: bool = True


async def resolve_sql_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Optional[SqlBinding]:
    """Resolve an SQL-audience handle to its binding, or None to deny.

    None means the proxy refuses the connection. An API-audience handle never resolves here
    (ADR-0015 dec 3); absent/expired/revoked/malformed all deny (dec 1-2).
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
    if record.get("audience") != AUDIENCE_SQL:
        return None
    return SqlBinding(
        user=record.get("user", ""),
        company_id=record["company_id"],
        read_only=bool(record.get("read_only", True)),
    )


def reader_role(company_id: str) -> str:
    """The per-tenant read-only role for a tenant (ADR-0011). UUID-validated before use."""
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_reader_{canonical.replace('-', '_')}"


def session_setup_statements(binding: SqlBinding) -> list[str]:
    """The statements the proxy runs on the backend before relaying the kernel's queries.

    SET ROLE assumes the single-tenant read-only role (so cross-tenant access fails at the
    GRANT level, ADR-0011); the read-only transaction default is belt-and-suspenders.
    """
    role = reader_role(binding.company_id)
    # role is derived from a validated UUID, so this identifier cannot carry injection.
    statements = [f'SET ROLE "{role}"']
    if binding.read_only:
        statements.append("SET default_transaction_read_only = on")
    return statements
