# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SQL proxy binding policy (ADR-0015 dec 5-6).

The proxy resolves an SQL-audience capability handle to its tenant, then opens the backend
connection as **that tenant's own login role** and assumes the tenant's read-only role (ADR-0011
keystone). This module is the pure policy: resolve the handle, derive the per-tenant login, its
credential and its reader role, and produce the per-connection setup statements. The proxy itself
(orchestration + Postgres wire) lives in proxy.py and server.py.

Why a per-tenant login rather than one privileged login that ``SET ROLE``s: the proxy relays the
kernel's bytes verbatim, so the kernel can do anything the upstream *session user* can —
``RESET ROLE``, ``SET ROLE`` into any role that user is a member of, ``set_config('role', ...)``.
A session user that may assume every tenant's reader hands every tenant to every kernel. The
session user is therefore ``tenant_sqlproxy_<uuid>``, a member of its own tenant's reader and of
nothing that reaches another tenant, and Postgres — not statement filtering — refuses the way out.

Its password is ``HMAC-SHA256(tenant key, tenant)``: the key exists only in the proxy, the database
holds only the SCRAM verifier the proxy enrols (``enrol_statement``), and nothing is stored.

The store key/record shape is the contract shared with the minter
(services/notebook_hub/capabilities.py); this is the SQL verifier's copy.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

AUDIENCE_SQL = "sql"
_KEY_PREFIX = "nbcap:"
# Present while the handle's company is suspended (ADR-0033 dec 3): its handles do not resolve.
# Written and removed by the platform API with the company's active flag; shared contract
# (services/api_gateway/capability_auth.py).
_SUSPENDED_PREFIX = "nbcap-suspended:"


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
    (ADR-0015 dec 3); absent/expired/revoked/malformed all deny (dec 1-2) — a record that is not
    an object, or whose tenant is missing or not a UUID, denies rather than raising — and so does
    a handle whose tenant is suspended.
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
    if not isinstance(record, dict):
        return None
    if record.get("audience") != AUDIENCE_SQL:
        return None
    try:
        # Canonical, or deny: the tenant becomes a role name and a schema name below.
        company_id = str(uuid.UUID(str(record.get("company_id") or "")))
    except ValueError:
        return None
    if await aget(f"{_SUSPENDED_PREFIX}{company_id}") is not None:
        return None
    return SqlBinding(
        user=str(record.get("user", "")),
        company_id=company_id,
        read_only=bool(record.get("read_only", True)),
    )


def reader_role(company_id: str) -> str:
    """The per-tenant read-only role for a tenant (ADR-0011). UUID-validated before use."""
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_reader_{canonical.replace('-', '_')}"


def tenant_login_role(company_id: str) -> str:
    """The per-tenant LOGIN role a kernel's session is opened as (13-notebook-sql-proxy-role.sh).

    Confined by the database to this one tenant: NOINHERIT, a member of its own reader and of
    nothing that reaches another tenant. UUID-validated, same derivation as the reader role.
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_sqlproxy_{canonical.replace('-', '_')}"


# Domain-separates this derivation from any other use of the key. Bumping the version rotates every
# tenant credential at once: each login is re-enrolled on its next refused authentication.
_LOGIN_CONTEXT = b"industryflow/notebook-sql-proxy/tenant-login/v1:"
# A key shorter than this is not one worth deriving credentials from.
MIN_TENANT_KEY_BYTES = 32


def tenant_login_password(key: bytes, company_id: str) -> str:
    """The tenant login's password: ``HMAC-SHA256(key, context || tenant)``, base64url.

    Deterministic, so nothing is stored and concurrent kernels of one tenant agree on it; keyed by a
    secret only the proxy holds, so knowing a tenant id (which anyone who can list roles does)
    yields nothing.
    """
    if len(key) < MIN_TENANT_KEY_BYTES:
        raise ValueError(f"the tenant login key must be at least {MIN_TENANT_KEY_BYTES} bytes")
    canonical = str(uuid.UUID(str(company_id)))
    digest = hmac.new(key, _LOGIN_CONTEXT + canonical.encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


_VERIFIER_RE = re.compile(
    r"^SCRAM-SHA-256\$[0-9]{4,7}:[A-Za-z0-9+/]+=*\$[A-Za-z0-9+/]+=*:[A-Za-z0-9+/]+=*$"
)


def enrol_statement(company_id: str, verifier: str) -> str:
    """The one statement an enrolment session runs: install a tenant login's SCRAM verifier.

    Both values are validated to a closed character set before they are quoted, so neither can carry
    a quote; the database function re-validates the verifier anyway.
    """
    canonical = str(uuid.UUID(str(company_id)))
    if not _VERIFIER_RE.match(verifier):
        raise ValueError("not a SCRAM-SHA-256 verifier")
    return f"SELECT public.notebook_sql_proxy_enrol('{canonical}'::uuid, '{verifier}')"


def tenant_schema(company_id: str) -> str:
    """The tenant's schema (ADR-0003). UUID-validated, same derivation as the reader role."""
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


def session_setup_statements(binding: SqlBinding) -> list[str]:
    """The statements the proxy runs on the backend before relaying the kernel's queries.

    The session user is the tenant's own NOINHERIT login, which reads nothing until it SET ROLEs.
    SET ROLE assumes the single-tenant read-only role (so cross-tenant access fails at the
    GRANT level, ADR-0011); search_path points at the tenant's schema so unqualified table names
    resolve to it (the reader can see no other schema anyway — ergonomics, not a trust boundary);
    the read-only transaction default is belt-and-suspenders. None of these is the boundary: a
    kernel may undo every one of them, and lands on a principal confined to its own tenant.
    """
    role = reader_role(binding.company_id)
    schema = tenant_schema(binding.company_id)
    # role and schema are derived from a validated UUID, so neither identifier can carry injection.
    statements = [f'SET ROLE "{role}"', f'SET search_path TO "{schema}"']
    if binding.read_only:
        statements.append("SET default_transaction_read_only = on")
    return statements
