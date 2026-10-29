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
(services/notebook_hub/capabilities.py); this is the SQL verifier's copy. Each presented handle's
outcome and each session's opening and closing are recorded into the capability-use record
(ADR-0042), by ``record_resolution`` and ``record_session``.
"""
from __future__ import annotations

import logging

import base64
import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Tuple

from industryflow_contracts import capability_audit as audit
from industryflow_contracts import tenancy

AUDIENCE_SQL = "sql"
# This plane's name in the capability-use record (ADR-0042).
PLANE = "sql_proxy"
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
    # The non-secret identifier the minter bound into the entry (ADR-0042 dec 2); None for an entry
    # minted before the record existed.
    handle_id: Optional[str] = None


async def resolve_sql_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Optional[SqlBinding]:
    """Resolve an SQL-audience handle to its binding, or None to deny.

    None means the proxy refuses the connection. An API-audience handle never resolves here
    (ADR-0015 dec 3); absent/expired/revoked/malformed all deny (dec 1-2) — a record that is not
    an object, or whose tenant is missing or not a UUID, denies rather than raising — and so does
    a handle whose tenant is suspended.
    """
    binding, _reason, _facts = await resolve_sql_outcome(aget, handle)
    return binding


async def resolve_sql_outcome(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Tuple[Optional[SqlBinding], Optional[str], Dict[str, str]]:
    """``resolve_sql_binding``, also saying why it denied and what the entry named, for the record
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
    if record.get("audience") != AUDIENCE_SQL:
        return None, "wrong_audience", facts
    try:
        # Canonical, or deny: the tenant becomes a role name and a schema name below.
        company_id = tenancy.canonical_company_id(record.get("company_id") or "")
    except ValueError:
        return None, "malformed_entry", facts
    if await aget(f"{_SUSPENDED_PREFIX}{company_id}") is not None:
        return None, "tenant_suspended", facts
    return SqlBinding(
        user=str(record.get("user", "")),
        company_id=company_id,
        read_only=bool(record.get("read_only", True)),
        handle_id=facts.get("handle_id"),
    ), None, facts


def _entry_facts(record: dict) -> Dict[str, str]:
    """What an entry says that a record may carry: user, tenant and audience when well-formed, and
    the handle's non-secret identifier."""
    facts: Dict[str, str] = {}
    try:
        facts["company_id"] = tenancy.canonical_company_id(record.get("company_id") or "")
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


async def record_resolution(store, handle: str, binding: Optional[SqlBinding],
                            reason: Optional[str], facts: Dict[str, str], *, source: Optional[str] = None) -> None:
    """Record a presented handle's outcome at the proxy (ADR-0042 dec 1, 5): its first use here once,
    and every refusal. An entry minted before the record existed has no identifier and no first use."""
    if binding is not None:
        if binding.handle_id is None:
            return
        fields = audit.event(audit.FIRST_USE, plane=PLANE, user=binding.user,
                             company_id=binding.company_id, audience=AUDIENCE_SQL,
                             handle_id=binding.handle_id)
        keys, args = audit.first_use_script_args(fields)
        await store.eval(audit.FIRST_USE_SCRIPT, len(keys), *keys, *args)
        return
    # A value that named no entry is recorded by digest once per window, a real entry's refusal every
    # time and without a digest (ADR-0042 dec 2, 8).
    how, *call = audit.refusal(plane=PLANE, handle=handle, reason=reason, facts=facts, source=source)
    if how == "once":
        keys, args = call
        if await store.eval(audit.UNKNOWN_REFUSAL_SCRIPT, len(keys), *keys, *args) == -1:
            logging.getLogger(__name__).warning(
                "capability audit: %s presented more unknown handles than are recorded; the rest of "
                "this minute is not recorded (ADR-0042 dec 9)", source)
    else:
        await store.xadd(**call[0])


async def record_session(store, kind: str, binding: SqlBinding, *, reason: Optional[str] = None,
                         detail: Optional[str] = None) -> None:
    """Record an SQL session's opening or closing (ADR-0042 dec 1). Its statements are not recorded:
    the proxy relays them unread."""
    await store.xadd(**audit.append_args(audit.event(
        kind, plane=PLANE, user=binding.user, company_id=binding.company_id, audience=AUDIENCE_SQL,
        handle_id=binding.handle_id, reason=reason, detail=detail,
    )))


# The tenant's role names, from the one tenant-naming module (ADR-0003 dec 5, 7), UUID-validated there.
# ``reader_role`` — the per-tenant read-only role (ADR-0011).
reader_role = tenancy.reader_role_name
# ``tenant_login_role`` — the per-tenant LOGIN role a kernel's session is opened as
# (13-notebook-sql-proxy-role.sh). Confined by the database to this one tenant: NOINHERIT, a member of
# its own reader and of nothing that reaches another tenant.
tenant_login_role = tenancy.sql_login_name


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
    canonical = tenancy.canonical_company_id(company_id)
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
    canonical = tenancy.canonical_company_id(company_id)
    if not _VERIFIER_RE.match(verifier):
        raise ValueError("not a SCRAM-SHA-256 verifier")
    return f"SELECT public.notebook_sql_proxy_enrol('{canonical}'::uuid, '{verifier}')"


# The tenant's schema (ADR-0003 dec 5), from the same module as the role names.
tenant_schema = tenancy.schema_name


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
