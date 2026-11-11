# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Entering the notebook origin (ADR-0048 dec 3, 4): the ticket and the notebook session.

The notebook origin runs code the platform does not trust and is granted nothing by the platform
origin, so the platform session cannot be its credential. The platform demand-mints a **ticket** from
the session (ADR-0015 dec 4): a capability of the ``notebook`` audience in the shared store, living
seconds, naming one user, tenant and session, and recorded like every capability (ADR-0042). The
notebook origin spends it once for a **notebook session**: an opaque handle naming the user and the
platform session it was opened from. No capability plane resolves the ``notebook`` audience, so the
ticket is no data credential, and nothing exchanges a notebook session for a platform one.

The notebook session is checked on every hub request through the platform's one session
verification (``users.admit_session``); it is bound to the session, not to an access token, so refresh
rotation leaves it, and it ends with the session (``refresh_tokens.session_live``).
"""
from __future__ import annotations

import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from industryflow_contracts import capability_audit as audit

AUDIENCE_NOTEBOOK = "notebook"
# The capability store's namespace every plane shares (ADR-0015 dec 1).
_TICKET_PREFIX = "nbcap:"
# Long enough for the page to hand the ticket to the notebook origin, short enough to be worthless
# afterwards.
TICKET_TTL_SECONDS = 30
_SESSION_PREFIX = "notebook-session:"
# The notebook origin's own cookie: host-only, httpOnly, never a platform cookie.
SESSION_COOKIE = "if_nb"
PLANE = "api_gateway"
_HANDLE_BYTES = 32


@dataclass(frozen=True)
class Ticket:
    user: str
    company_id: str
    session_id: str
    issued_at: float
    handle_id: str


@dataclass(frozen=True)
class NotebookSession:
    user: str
    session_id: str
    issued_at: float
    credential: str


def _ticket_key(ticket: str) -> str:
    return f"{_TICKET_PREFIX}{ticket}"


def _session_key(handle: str) -> str:
    return f"{_SESSION_PREFIX}{handle}"


async def mint_ticket(redis, *, user: str, company_id: str, session_id: str) -> str:
    """Mint a ticket for the user's session; its ``minted`` event is written with it (ADR-0042 dec 3).
    The tenant is the verified user's, never the caller's (ADR-0015 dec 4)."""
    if not user or not session_id:
        raise ValueError("a ticket names one user and one session")
    record = {
        "user": str(user),
        "company_id": str(uuid.UUID(str(company_id))),
        "audience": AUDIENCE_NOTEBOOK,
        "read_only": True,
        "sid": str(session_id),
        "iat": time.time(),
        "handle_id": audit.new_handle_id(),
    }
    ticket = secrets.token_urlsafe(_HANDLE_BYTES)
    event = audit.event(audit.MINTED, plane=PLANE, user=record["user"], company_id=record["company_id"],
                        audience=AUDIENCE_NOTEBOOK, handle_id=record["handle_id"])
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(_ticket_key(ticket), json.dumps(record), ex=TICKET_TTL_SECONDS)
        pipe.xadd(**audit.append_args(event))
        await pipe.execute()
    return ticket


def _ticket(record: dict) -> Optional[Ticket]:
    try:
        return Ticket(user=str(record["user"]), company_id=str(uuid.UUID(str(record["company_id"]))),
                      session_id=str(record["sid"]), issued_at=float(record["iat"]),
                      handle_id=str(uuid.UUID(str(record["handle_id"]))))
    except (KeyError, TypeError, ValueError):
        return None


async def spend_ticket(redis, ticket: str, *, source: Optional[str] = None) -> Optional[Ticket]:
    """The ticket's binding, removing it so it is spent once, or None. It is resolved as every
    capability is (``capability_auth.resolve_capability_outcome``: audience, a suspended tenant, a store
    not rebuilt), a refusal is recorded as at every plane, and the removal is recorded as the ticket's
    consumption atomically with it (ADR-0042 dec 1, 3, 8). A handle of another audience is refused and
    left where it is."""
    from capability_auth import record_resolution, resolve_capability_outcome

    binding, reason, facts = await resolve_capability_outcome(redis.get, ticket,
                                                              expected_audience=AUDIENCE_NOTEBOOK)
    if binding is None:
        await record_resolution(redis, ticket or "", None, reason, facts, source=source)
        return None
    raw = await redis.get(_ticket_key(ticket))
    try:
        spent = _ticket(json.loads(raw)) if raw is not None else None
    except (TypeError, ValueError):
        spent = None
    if spent is None:
        return None
    fields = audit.event(audit.CONSUMED, plane=PLANE, user=spent.user, company_id=spent.company_id,
                         audience=AUDIENCE_NOTEBOOK, handle_id=spent.handle_id)
    keys, args = audit.delete_recorded_args(_ticket_key(ticket), fields)
    if int(await redis.eval(audit.DELETE_RECORDED_SCRIPT, len(keys), *keys, *args)) != 1:
        return None  # spent by a concurrent exchange
    return spent


async def open_session(redis, *, user: str, session_id: str, credential: str, ttl_seconds: int) -> str:
    """Open a notebook session bound to the platform session; returns its handle."""
    handle = secrets.token_urlsafe(_HANDLE_BYTES)
    record = {"user": str(user), "sid": str(session_id), "iat": time.time(), "cred": credential}
    await redis.set(_session_key(handle), json.dumps(record), ex=ttl_seconds)
    return handle


async def read_session(redis, handle: Optional[str]) -> Optional[NotebookSession]:
    """The notebook session a handle names, or None."""
    if not handle:
        return None
    raw = await redis.get(_session_key(handle))
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        return NotebookSession(user=str(record["user"]), session_id=str(record["sid"]),
                               issued_at=float(record["iat"]), credential=str(record["cred"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
