# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Entering the notebook origin, and the SSO proxy's session check (ADR-0048 dec 3, 4; ADR-0014 dec 2).

* ``POST /api/notebook-ticket`` — on the platform origin, from the platform session, CSRF-checked: a
  ticket and the notebook origin it enters.
* ``POST /auth/notebook-entry`` — the notebook origin's one public entry, reached through its SSO proxy:
  spends the ticket from a form body sent by a platform page, and opens a notebook session in a
  host-only cookie of the notebook origin.
* ``GET /auth/verify`` — the SSO proxy's ``auth_request`` on every hub request: the notebook session,
  through the platform's one session verification, answered with the verified identity in ``X-IF-*``
  headers. It reads no platform cookie (ADR-0048 dec 2).

None of this grants data access; what a kernel may reach is minted separately by the spawner
(ADR-0012).
"""
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status

import notebook_entry
from config import get_settings
from dependencies import EDGE_PEERS, get_current_user_with_company
from industryflow_contracts import capability_audit as audit
from messaging import refresh_tokens
from messaging.redis_client import redis_client
from models.user import User
from origins import allowed_origins
from routers.auth_tokens import presented_access_claims
from users import admit_session, get_user_manager

# Header names the SSO proxy reads (must match services/notebook_hub/identity.py).
HEADER_USER = "X-IF-User"
HEADER_COMPANY_ID = "X-IF-Company-Id"
HEADER_ROLE = "X-IF-Role"
# Where the notebook origin sends a browser that has entered: its root, which the proxy maps to the hub.
NOTEBOOK_HOME = "/"

router = APIRouter(tags=["Auth"])

_NO_STORE = {"Cache-Control": "no-store"}


def _notebook_origin() -> str:
    origin = get_settings().NOTEBOOK_ORIGIN
    if not origin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebooks are not deployed")
    return origin


@router.post("/api/notebook-ticket")
async def mint_ticket(request: Request, response: Response,
                      user: User = Depends(get_current_user_with_company)) -> dict:
    """A ticket into the notebook origin for the session this request is made in (ADR-0048 dec 3).
    A session that cannot be continued (a sign-in without a refresh token) opens no notebook session,
    since nothing would end it."""
    origin = _notebook_origin()
    claims = presented_access_claims(request) or {}
    session_id = claims.get("sid")
    try:
        live = await refresh_tokens.session_live(session_id)
    except Exception:  # noqa: BLE001 - an unknown answer is not a yes
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Session store not available")
    if not live:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This sign-in cannot enter notebooks; sign in from the platform page")
    ticket = await notebook_entry.mint_ticket(redis_client.redis, user=str(user.id),
                                              company_id=str(user.company_id), session_id=session_id)
    response.headers.update(_NO_STORE)
    return {"ticket": ticket, "entry": f"{origin}/enter", "origin": origin}


def _refused(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


@router.post("/auth/notebook-entry")
async def enter(request: Request, ticket: str = Form(...), user_manager=Depends(get_user_manager)) -> Response:
    """Spend a ticket for a notebook session (ADR-0048 dec 3, 4).

    Sent by a platform page into the notebook origin, so its Origin is the platform's and its Host the
    notebook origin's: a page elsewhere cannot make a browser enter with someone else's ticket, and the
    session cookie lands on the notebook origin only.
    """
    origin = _notebook_origin()
    if request.headers.get("origin", "").rstrip("/").lower() not in allowed_origins():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Entry is sent by a platform page")
    if (request.headers.get("host") or "").lower() != urlsplit(origin).netloc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Entry is made on the notebook origin")
    source = await audit.client_source(request.client.host if request.client else None,
                                       request.headers.get("x-real-ip"), EDGE_PEERS)
    spent = await notebook_entry.spend_ticket(redis_client.redis, ticket, source=source)
    if spent is None:
        raise _refused("Ticket invalid, spent or expired")
    user = await _user(user_manager, spent.user)
    if user is None or str(user.company_id) != spent.company_id:
        raise _refused("User not found or inactive")
    if not await _session_admits(user, spent.session_id, spent.issued_at):
        raise _refused("Session ended")
    handle = await notebook_entry.open_session(
        redis_client.redis, user=spent.user, session_id=spent.session_id,
        credential=refresh_tokens.credential_fingerprint(user.hashed_password),
        ttl_seconds=refresh_tokens.session_ttl())
    s = get_settings()
    response = Response(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": NOTEBOOK_HOME, **_NO_STORE})
    # Host-only, httpOnly, and sent only within the site: never a platform cookie, never script-readable.
    response.set_cookie(notebook_entry.SESSION_COOKIE, handle, httponly=True, secure=s.COOKIE_SECURE,
                        samesite="strict", path="/")
    return response


@router.get("/auth/verify")
async def verify_session(request: Request, user_manager=Depends(get_user_manager)) -> Response:
    """Return 204 with the verified identity in ``X-IF-*`` headers for the SSO proxy, or 401.

    The notebook session admits while its platform session does (ADR-0048 dec 4): the session can still
    be continued, it has not ended, the account is active, its company not suspended, and its password
    is the one the notebook session was opened under. The identity is the account's now, so a changed
    tenant or role reaches the hub's re-check (ADR-0014 dec 7).
    """
    nb = await notebook_entry.read_session(redis_client.redis,
                                           request.cookies.get(notebook_entry.SESSION_COOKIE))
    if nb is None:
        raise _refused("No notebook session")
    user = await _user(user_manager, nb.user)
    if user is None or not user.company_id:
        raise _refused("User not found or inactive")
    if refresh_tokens.credential_fingerprint(user.hashed_password) != nb.credential:
        raise _refused("Session ended by a credential change")
    if not await _session_admits(user, nb.session_id, nb.issued_at):
        raise _refused("Session ended")
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            HEADER_USER: str(user.id),
            HEADER_COMPANY_ID: str(user.company_id),
            HEADER_ROLE: user.role or "",
        },
    )


async def _user(user_manager, user_id: str):
    """The active account, or None."""
    from fastapi_users.exceptions import UserNotExists

    try:
        user = await user_manager.get(uuid.UUID(user_id))
    except (UserNotExists, ValueError):
        return None
    return user if user is not None and user.is_active else None


async def _session_admits(user, session_id: str, issued_at: float) -> bool:
    """The platform session can still be continued, and admits (``users.admit_session``)."""
    try:
        live = await refresh_tokens.session_live(session_id)
    except Exception:  # noqa: BLE001 - an unknown answer is not a yes
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Session store not available")
    return live and await admit_session(user, session_id, issued_at)
