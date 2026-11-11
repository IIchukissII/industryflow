# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Entering the notebook origin (ADR-0048 dec 2-4) and the SSO proxy's session check (ADR-0014 dec 2).

Through the app, on the capability store (a real Redis in CI): a platform session mints a ticket, the
notebook origin spends it once for a notebook session, and that session admits exactly while its
platform session does.
"""
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from industryflow_contracts import capability_audit as audit
from industryflow_contracts import testing
from pydantic import ValidationError

import config
import main
import notebook_entry
import users
from capability_auth import resolve_capability
from messaging import redis_client as redis_module
from messaging import refresh_tokens
from users import get_user_manager

PLATFORM = "https://app.example.com"  # in CORS_ORIGINS (conftest)
NOTEBOOK = "https://nb.example.com"
NB_HOST = "nb.example.com"


@pytest.fixture
def world(monkeypatch):
    store = testing.AnyLoopRedis(encoding="utf-8", decode_responses=True)
    monkeypatch.setattr(redis_module.redis_client, "redis", store)
    monkeypatch.setattr(config.get_settings(), "NOTEBOOK_ORIGIN", NOTEBOOK)
    user = SimpleNamespace(id=uuid.uuid4(), email="u@example.com", company_id=uuid.uuid4(), role="engineer",
                           is_active=True, is_superuser=False, is_verified=True, hashed_password="hash-1")

    class Manager:
        async def get(self, user_id):
            if user_id != user.id:
                from fastapi_users.exceptions import UserNotExists
                raise UserNotExists()
            return user

        def parse_id(self, value):
            return uuid.UUID(str(value))

    main.app.dependency_overrides[get_user_manager] = lambda: Manager()
    yield SimpleNamespace(client=TestClient(main.app, raise_server_exceptions=False), user=user, store=store)
    main.app.dependency_overrides.clear()


def _signed_in(w, sid="sid-a"):
    """A browser session: a refresh token and an access token in session ``sid``."""
    import asyncio

    asyncio.run(refresh_tokens.create_refresh_token(str(w.user.id), w.user.hashed_password, sid))
    return asyncio.run(users.get_jwt_strategy().write_token(w.user, sid))


def _mint(w, access, origin=PLATFORM):
    return w.client.post("/api/notebook-ticket", headers={
        "Cookie": f"if_access={access}; if_csrf=c", "X-CSRF-Token": "c", "Origin": origin})


def _enter(w, ticket, origin=PLATFORM, host=NB_HOST):
    return w.client.post("/auth/notebook-entry", data={"ticket": ticket}, follow_redirects=False,
                         headers={"Origin": origin, "Host": host})


def _verify(w, nb_cookie, extra=""):
    return w.client.get("/auth/verify", headers={"Cookie": f"{extra}if_nb={nb_cookie}"})


def _entered(w, sid="sid-a"):
    minted = _mint(w, _signed_in(w, sid))
    assert minted.status_code == 200, minted.text
    entered = _enter(w, minted.json()["ticket"])
    assert entered.status_code == 303, entered.text
    return entered


def _on(w, call):
    """``call(store)`` awaited on an event loop of its own, as the store is used outside the app."""
    import asyncio

    async def run():
        return await call(w.store)
    return asyncio.run(run())


def _nb_cookie(response):
    header = response.headers["set-cookie"]
    assert header.startswith(f"{notebook_entry.SESSION_COOKIE}=")
    return header.split(";")[0].split("=", 1)[1], header.lower()


def test_a_platform_session_enters_and_the_proxy_gets_the_identity(world):
    minted = _mint(world, _signed_in(world))
    assert minted.status_code == 200 and minted.headers["cache-control"] == "no-store"
    body = minted.json()
    assert body["origin"] == NOTEBOOK and body["entry"] == f"{NOTEBOOK}/enter"
    entered = _enter(world, body["ticket"])
    assert entered.status_code == 303 and entered.headers["location"] == "/"
    cookie, header = _nb_cookie(entered)
    # Host-only (no Domain), script cannot read it, and it stays within the site.
    assert "httponly" in header and "samesite=strict" in header and "domain=" not in header
    ok = _verify(world, cookie)
    assert ok.status_code == 204
    assert ok.headers["X-IF-User"] == str(world.user.id)
    assert ok.headers["X-IF-Company-Id"] == str(world.user.company_id)
    assert ok.headers["X-IF-Role"] == "engineer"


def test_a_ticket_is_spent_once(world):
    ticket = _mint(world, _signed_in(world)).json()["ticket"]
    assert _enter(world, ticket).status_code == 303
    assert _enter(world, ticket).status_code == 401


def test_entry_is_sent_by_a_platform_page_to_the_notebook_origin(world):
    access = _signed_in(world)
    # Another page cannot make the browser enter with a ticket (login forgery); the notebook origin's
    # own script is no platform page either.
    assert _enter(world, _mint(world, access).json()["ticket"], origin="https://evil.example").status_code == 403
    assert _enter(world, _mint(world, access).json()["ticket"], origin=NOTEBOOK).status_code == 403
    # The session cookie lands on the notebook origin only.
    assert _enter(world, _mint(world, access).json()["ticket"], host="app.example.com").status_code == 403


def test_the_ticket_is_no_data_credential(world):
    import asyncio

    ticket = _mint(world, _signed_in(world)).json()["ticket"]
    assert _on(world, lambda st: resolve_capability(st.get, ticket)) is None


def test_the_ticket_lives_seconds_and_names_user_tenant_and_session(world):
    import asyncio

    ticket = _mint(world, _signed_in(world, "sid-t")).json()["ticket"]
    key = f"nbcap:{ticket}"
    record = json.loads(_on(world, lambda st: st.get(key)))
    assert 0 < _on(world, lambda st: st.ttl(key)) <= notebook_entry.TICKET_TTL_SECONDS
    assert record["audience"] == "notebook" and record["sid"] == "sid-t"
    assert record["user"] == str(world.user.id) and record["company_id"] == str(world.user.company_id)


def test_mint_and_consumption_are_recorded(world):
    import asyncio

    ticket = _mint(world, _signed_in(world)).json()["ticket"]
    _enter(world, ticket)
    events = [audit.decode(f) for _, f in _on(world, lambda st: st.xrange(audit.STREAM_KEY))]
    kinds = [(e["kind"], e["plane"], e["audience"]) for e in events]
    assert ("minted", "api_gateway", "notebook") in kinds and ("consumed", "api_gateway", "notebook") in kinds
    assert ticket not in json.dumps(events)


def test_an_unknown_ticket_is_refused_and_recorded(world):
    import asyncio

    assert _enter(world, "not-a-ticket").status_code == 401
    events = [audit.decode(f) for _, f in _on(world, lambda st: st.xrange(audit.STREAM_KEY))]
    assert [e["kind"] for e in events] == ["refused"]


def test_the_proxy_honours_no_platform_cookie(world):
    access = _signed_in(world)
    assert world.client.get("/auth/verify", headers={"Cookie": f"if_access={access}"}).status_code == 401
    assert world.client.get("/auth/verify", headers={"Authorization": f"Bearer {access}"}).status_code == 401


def test_the_notebook_session_survives_refresh_rotation(world):
    cookie, _ = _nb_cookie(_entered(world))
    rt = None
    import asyncio
    rt = asyncio.run(refresh_tokens.create_refresh_token(str(world.user.id), world.user.hashed_password, "sid-a"))
    rotated = world.client.post("/auth/refresh", json={"refresh_token": rt})
    assert rotated.status_code == 200, rotated.text
    assert _verify(world, cookie).status_code == 204


@pytest.mark.parametrize("end", ["logout", "all_sessions", "lapse"])
def test_the_notebook_session_ends_with_its_platform_session(world, end):
    import asyncio

    cookie, _ = _nb_cookie(_entered(world))
    assert _verify(world, cookie).status_code == 204
    if end == "logout":
        asyncio.run(refresh_tokens.end_session("sid-a"))
    elif end == "all_sessions":
        asyncio.run(refresh_tokens.end_all_sessions(str(world.user.id)))
    else:  # the session was never continued and its last refresh token expired
        _on(world, lambda st: st.delete(refresh_tokens.live_session_key("sid-a")))
    assert _verify(world, cookie).status_code == 401


def test_other_sessions_ending_leave_it(world):
    import asyncio

    cookie, _ = _nb_cookie(_entered(world, "sid-a"))
    _signed_in(world, "sid-b")
    asyncio.run(refresh_tokens.end_session("sid-b"))
    assert _verify(world, cookie).status_code == 204


@pytest.mark.parametrize("change", ["password", "deactivated", "tenant_suspended"])
def test_the_account_changing_ends_it(world, company_flags, change):
    cookie, _ = _nb_cookie(_entered(world))
    if change == "password":
        world.user.hashed_password = "hash-2"  # set outside the gateway, as the admin CLI does
    elif change == "deactivated":
        world.user.is_active = False
    else:
        import company_status

        company_flags.inactive.add(str(world.user.company_id))
        company_status.clear()
    assert _verify(world, cookie).status_code == 401


def test_a_role_change_reaches_the_hub(world):
    cookie, _ = _nb_cookie(_entered(world))
    world.user.role = "observer"
    assert _verify(world, cookie).headers["X-IF-Role"] == "observer"


def test_a_session_that_cannot_be_continued_mints_no_ticket(world):
    import asyncio

    access = asyncio.run(users.get_jwt_strategy().write_token(world.user, "sid-bearer-only"))
    assert _mint(world, access).status_code == 409


def test_no_notebook_origin_no_ticket(world, monkeypatch):
    monkeypatch.setattr(config.get_settings(), "NOTEBOOK_ORIGIN", None)
    assert _mint(world, _signed_in(world)).status_code == 404


@pytest.mark.parametrize("path", ["/auth/logout", "/auth/refresh", "/auth/login", "/api/notebook-ticket"])
def test_the_notebook_origin_sends_the_platform_no_state_changing_request(world, path):
    # Same site: the browser attaches the platform cookies, so the origin is what refuses it.
    access = _signed_in(world)
    r = world.client.post(path, headers={"Origin": NOTEBOOK, "Cookie": f"if_access={access}"})
    assert r.status_code == 403 and r.json()["detail"] == "Cross-origin request refused"


def test_the_platform_page_and_non_browser_callers_are_unaffected(world):
    assert world.client.post("/auth/logout", headers={"Origin": PLATFORM}).status_code == 204
    assert world.client.post("/auth/logout", headers={"Origin": "http://testserver"}).status_code == 204
    assert world.client.post("/auth/logout").status_code == 204


@pytest.mark.parametrize("value", ["https://nb.example.com/path", "nb.example.com", "https://nb.example.com?x",
                                   "http://nb.example.com"])
def test_the_notebook_origin_is_an_origin(value):
    with pytest.raises(ValidationError, match="ADR-0048"):
        config.Settings(CORS_ORIGINS=PLATFORM, NOTEBOOK_ORIGIN=value)


def test_the_notebook_origin_is_in_no_allowlist():
    with pytest.raises(ValidationError, match="ADR-0048 dec 2"):
        config.Settings(CORS_ORIGINS=f"{PLATFORM}, {NOTEBOOK}", NOTEBOOK_ORIGIN=f"{NOTEBOOK.upper()}/")
    assert config.Settings(CORS_ORIGINS=PLATFORM, NOTEBOOK_ORIGIN=f"{NOTEBOOK}/").NOTEBOOK_ORIGIN == NOTEBOOK


def test_verify_header_names_match_hub_contract():
    # The proxy and the hub read these exact names (services/notebook_hub/identity.py).
    from routers import notebook_entry as route

    assert (route.HEADER_USER, route.HEADER_COMPANY_ID, route.HEADER_ROLE) == (
        "X-IF-User", "X-IF-Company-Id", "X-IF-Role")


def test_the_notebook_origin_is_refused_whatever_host_it_addresses(world):
    # Addressed to the notebook host itself, its own origin is still no platform page.
    r = world.client.post("/auth/logout", headers={"Origin": NOTEBOOK, "Host": NB_HOST})
    assert r.status_code == 403 and r.json()["detail"] == "Cross-origin request refused"
    r = world.client.post("/auth/logout", headers={"Origin": NOTEBOOK.upper(), "Host": "testserver"})
    assert r.status_code == 403


@pytest.mark.parametrize("step", ["entry", "verify"])
def test_an_account_held_to_a_password_change_does_not_enter(world, step):
    cookie, _ = _nb_cookie(_entered(world)) if step == "verify" else (None, None)
    ticket = _mint(world, _signed_in(world, "sid-h")).json()["ticket"] if step == "entry" else None
    world.user.password_change_required = True
    r = _enter(world, ticket) if step == "entry" else _verify(world, cookie)
    assert r.status_code == 403 and r.json()["detail"] == users.PASSWORD_CHANGE_REQUIRED
