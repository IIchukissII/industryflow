# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sensor websockets authenticate like the rest of the browser session (ADR-0004 dec 3, 7).

Only the httpOnly access cookie opens a socket (never a ?token= in the URL, which ends up in
access logs), an inactive user cannot connect, and an open socket ends with its session: at the
access token's expiry, or once its user is deactivated. The names cache never crosses tenants.
"""
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from starlette.websockets import WebSocketDisconnect

import main
from routers import websocket as ws
from users import ACCESS_COOKIE

ENDPOINTS = ["/ws/sensors", f"/ws/sensors/{uuid.uuid4()}"]


def _token(user_id, exp_in=600):
    return jwt.encode({"sub": str(user_id), "aud": "fastapi-users:auth", "exp": int(time.time() + exp_in)},
                      ws.SECRET, algorithm="HS256")


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(users={})

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
