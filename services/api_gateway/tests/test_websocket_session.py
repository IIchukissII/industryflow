# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sensor websockets authenticate like the rest of the browser session (ADR-0004 dec 3, 7).

Only the httpOnly access cookie opens a socket (never a ?token= in the URL, which ends up in
access logs), an inactive user cannot connect, and an open socket ends with its session (ADR-0004
dec 9): at the access token's expiry, or at its next re-check once its user is deactivated, its
company is suspended, or the session it was opened in has ended (logout, or all of the user's
sessions ended). The names cache never crosses tenants.
"""
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from starlette.websockets import WebSocketDisconnect

import main
from messaging import refresh_tokens
from routers import websocket as ws
from users import ACCESS_COOKIE

ENDPOINTS = ["/ws/sensors", f"/ws/sensors/{uuid.uuid4()}"]


def _token(user_id, exp_in=600, sid=None, iat=None):
    claims = {"sub": str(user_id), "aud": "fastapi-users:auth", "exp": int(time.time() + exp_in),
              "iat": int(time.time()) if iat is None else iat}
    if sid:
        claims["sid"] = sid
    return jwt.encode(claims, ws.SECRET, algorithm="HS256")


class FakeStore:
    """The shared store's commands the session records use, in memory."""

    def __init__(self):
        self.kv, self.sets = {}, {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def getdel(self, key):
        return self.kv.pop(key, None)

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    async def srem(self, key, *members):
        self.sets.get(key, set()).difference_update(members)

    async def expire(self, key, ttl):
        pass

    async def hkeys(self, key):
        return []

    def pipeline(self, transaction=True):
        store = self

        class Pipe:
            def __init__(self):
                self.ops = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def __getattr__(self, name):
                return lambda *a, **k: self.ops.append((name, a, k))

            async def execute(self):
                for name, a, k in self.ops:
                    await getattr(store, name)(*a, **k)

        return Pipe()


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(users={}, store=FakeStore())
    monkeypatch.setattr(refresh_tokens.redis_client, "redis", state.store)

    async def load_active_user(user_id):
        u = state.users.get(str(user_id))
        return u if u is not None and u.is_active else None

    async def no_sensors():
        return {}

    monkeypatch.setattr(ws, "_load_active_user", load_active_user)
    monkeypatch.setattr(ws.redis_client, "get_all_sensors", no_sensors)

    def add_user(active=True):
        u = SimpleNamespace(id=uuid.uuid4(), email="u@example.com", company_id=uuid.uuid4(), is_active=active)
        state.users[str(u.id)] = u
        return u

    state.add_user = add_user
    return state


def _client(token=None):
    client = TestClient(main.app)
    if token:
        client.cookies.set(ACCESS_COOKIE, token)
    return client


def _close_code(client, path):
    with client.websocket_connect(path) as s:
        with pytest.raises(WebSocketDisconnect) as e:
            s.receive_json()
    return e.value.code


@pytest.mark.parametrize("path", ENDPOINTS)
def test_cookie_session_streams(world, path):
    u = world.add_user()
    with _client(_token(u.id)).websocket_connect(path) as s:
        assert s.receive_json()["type"] == "sensor_update"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_token_in_the_url_is_not_accepted(world, path):
    u = world.add_user()
    assert _close_code(_client(), f"{path}?token={_token(u.id)}") == ws.CLOSE_SESSION_EXPIRED


@pytest.mark.parametrize("path", ENDPOINTS)
def test_inactive_user_cannot_connect(world, path):
    u = world.add_user(active=False)
    assert _close_code(_client(_token(u.id)), path) == 1008


@pytest.mark.parametrize("path", ENDPOINTS)
def test_expired_cookie_asks_for_a_refresh(world, path):
    u = world.add_user()
    assert _close_code(_client(_token(u.id, exp_in=-5)), path) == ws.CLOSE_SESSION_EXPIRED


@pytest.mark.parametrize("path", ENDPOINTS)
def test_socket_closes_when_the_access_token_expires(world, path):
    u = world.add_user()
    with _client(_token(u.id, exp_in=1.5)).websocket_connect(path) as s:
        assert s.receive_json()["type"] == "sensor_update"
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(5):
                s.receive_json()
    assert e.value.code == ws.CLOSE_SESSION_EXPIRED


def test_socket_closes_when_the_user_is_deactivated(world, monkeypatch):
    monkeypatch.setattr(ws, "USER_RECHECK_SECONDS", 0)
    u = world.add_user()
    with _client(_token(u.id)).websocket_connect("/ws/sensors") as s:
        assert s.receive_json()["type"] == "sensor_update"
        u.is_active = False
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(5):
                s.receive_json()
    assert e.value.code == 1008


def _streams_then_closes(client, path="/ws/sensors", then=lambda: None):
    with client.websocket_connect(path) as s:
        assert s.receive_json()["type"] == "sensor_update"
        then()
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(5):
                s.receive_json()
    return e.value.code


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_socket_closes_when_its_company_is_suspended(world, monkeypatch, company_flags):
    import company_status

    monkeypatch.setattr(ws, "USER_RECHECK_SECONDS", 0)
    u = world.add_user()

    def suspend():
        company_flags.inactive.add(str(u.company_id))
        company_status.forget(u.company_id)

    assert _streams_then_closes(_client(_token(u.id)), then=suspend) == 1008


def test_a_suspended_companys_user_cannot_connect(world, company_flags):
    u = world.add_user()
    company_flags.inactive.add(str(u.company_id))
    assert _close_code(_client(_token(u.id)), "/ws/sensors") == 1008


def test_socket_closes_when_its_session_is_logged_out(world, monkeypatch):
    monkeypatch.setattr(ws, "USER_RECHECK_SECONDS", 0)
    u = world.add_user()
    sid = refresh_tokens.new_session_id()
    refresh = _run(refresh_tokens.create_refresh_token(str(u.id), "h", sid))
    code = _streams_then_closes(_client(_token(u.id, sid=sid)),
                                then=lambda: _run(refresh_tokens.revoke_refresh_token(refresh)))
    assert code == ws.CLOSE_SESSION_EXPIRED


def test_logout_of_another_session_leaves_the_socket_open(world, monkeypatch):
    monkeypatch.setattr(ws, "USER_RECHECK_SECONDS", 0)
    u = world.add_user()
    mine, other = refresh_tokens.new_session_id(), refresh_tokens.new_session_id()
    other_refresh = _run(refresh_tokens.create_refresh_token(str(u.id), "h", other))
    with _client(_token(u.id, sid=mine)).websocket_connect("/ws/sensors") as s:
        assert s.receive_json()["type"] == "sensor_update"
        _run(refresh_tokens.revoke_refresh_token(other_refresh))
        for _ in range(3):
            assert s.receive_json()["type"] == "sensor_update"


@pytest.mark.parametrize("with_sid", [True, False])
def test_socket_closes_when_all_the_users_sessions_end(world, monkeypatch, with_sid):
    monkeypatch.setattr(ws, "USER_RECHECK_SECONDS", 0)
    u = world.add_user()
    sid = None
    if with_sid:
        sid = refresh_tokens.new_session_id()
        _run(refresh_tokens.create_refresh_token(str(u.id), "h", sid))
    code = _streams_then_closes(_client(_token(u.id, sid=sid, iat=int(time.time()) - 5)),
                                then=lambda: _run(refresh_tokens.end_all_sessions(str(u.id))))
    assert code == ws.CLOSE_SESSION_EXPIRED


def test_a_token_issued_before_its_sessions_ended_cannot_open_a_socket(world):
    u = world.add_user()
    _run(refresh_tokens.end_all_sessions(str(u.id)))
    assert _close_code(_client(_token(u.id, iat=int(time.time()) - 10)), "/ws/sensors") == ws.CLOSE_SESSION_EXPIRED


def test_a_token_issued_after_the_sessions_ended_streams(world):
    u = world.add_user()
    _run(refresh_tokens.end_all_sessions(str(u.id)))
    with _client(_token(u.id, iat=int(time.time()) + 2)).websocket_connect("/ws/sensors") as s:
        assert s.receive_json()["type"] == "sensor_update"


def test_the_recheck_interval_is_configuration():
    assert ws.USER_RECHECK_SECONDS == ws.settings.WS_SESSION_RECHECK_SECONDS


@pytest.mark.asyncio
async def test_names_cache_is_per_tenant(monkeypatch):
    ws._names_cache.clear()
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    shared_sensor = str(uuid.uuid4())  # the same sensor_id existing in two tenants
    names = {tenant_a: "a-secret-name", tenant_b: "b-name"}
    loads = []

    async def load(company_id, sensor_ids):
        loads.append(company_id)
        return {sid: {"sensor_name": names[company_id], "equipment_name": "eq"} for sid in sensor_ids}

    monkeypatch.setattr(ws, "_load_names", load)
    a = await ws.enrich_sensors_with_names({shared_sensor: {}}, tenant_a)
    b = await ws.enrich_sensors_with_names({shared_sensor: {}}, tenant_b)
    a_again = await ws.enrich_sensors_with_names({shared_sensor: {}}, tenant_a)

    assert a[shared_sensor]["sensor_name"] == "a-secret-name"
    assert b[shared_sensor]["sensor_name"] == "b-name"
    assert a_again[shared_sensor]["sensor_name"] == "a-secret-name"
    assert loads == [tenant_a, tenant_b]  # each tenant loaded once, from its own schema


@pytest.mark.asyncio
async def test_names_cache_is_bounded(monkeypatch):
    ws._names_cache.clear()

    async def load(company_id, sensor_ids):
        return {}

    monkeypatch.setattr(ws, "_load_names", load)
    monkeypatch.setattr(ws, "_cache_max_tenants", 3)
    for _ in range(10):
        await ws.enrich_sensors_with_names({"s": {}}, str(uuid.uuid4()))
    assert len(ws._names_cache) == 3


# --- Origin (cross-site websocket hijacking, ADR-0004 dec 6) --------------------------------

def _connect_with_origin(world, monkeypatch, origin, path="/ws/sensors"):
    monkeypatch.setattr(ws.settings, "CORS_ORIGINS", "https://app.example.com, https://ops.example.com")
    u = world.add_user()
    client = _client(_token(u.id))
    headers = {"origin": origin} if origin else {}
    with client.websocket_connect(path, headers=headers) as s:
        try:
            return s.receive_json()["type"]
        except WebSocketDisconnect as e:
            return e.code


@pytest.mark.parametrize("path", ENDPOINTS)
def test_foreign_origin_is_refused_even_with_a_valid_cookie(world, monkeypatch, path):
    assert _connect_with_origin(world, monkeypatch, "https://evil.example", path) == 1008


@pytest.mark.parametrize("origin", ["https://app.example.com", "https://ops.example.com/"])
def test_allowlisted_origin_streams(world, monkeypatch, origin):
    assert _connect_with_origin(world, monkeypatch, origin) == "sensor_update"


def test_same_origin_as_the_addressed_host_streams(world, monkeypatch):
    # TestClient addresses host "testserver"; the edge forwards the browser's Host the same way.
    assert _connect_with_origin(world, monkeypatch, "https://testserver") == "sensor_update"


def test_lookalike_origin_is_refused(world, monkeypatch):
    assert _connect_with_origin(world, monkeypatch, "https://app.example.com.evil.example") == 1008
    assert _connect_with_origin(world, monkeypatch, "https://testserver.evil.example") == 1008


def test_no_origin_header_still_needs_a_session(world, monkeypatch):
    assert _connect_with_origin(world, monkeypatch, None) == "sensor_update"
    assert _close_code(_client(), "/ws/sensors") == ws.CLOSE_SESSION_EXPIRED
