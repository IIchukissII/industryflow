# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A session ends when its user's credential or account does (ADR-0004 dec 2).

Refresh tokens are indexed by user, so a password change or reset, deactivation and deletion
revoke all of them; and each token carries a fingerprint of the password hash, so a password
set outside the gateway (the admin CLI) also stops it from refreshing. Redis is an in-memory
fake with the handful of commands the store uses.
"""
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
import users
from messaging import redis_client as redis_module
from messaging import refresh_tokens
from routers.auth_tokens import REFRESH_COOKIE
from users import get_user_manager


class FakeRedis:
    def __init__(self):
        self.kv, self.sets, self.hashes = {}, {}, {}

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def getdel(self, key):
        return self.kv.pop(key, None)

    async def delete(self, *keys):
        n = 0
        for k in keys:
            n += int(self.kv.pop(k, None) is not None or self.sets.pop(k, None) is not None
                     or self.hashes.pop(k, None) is not None)
        return n

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    async def srem(self, key, *members):
        self.sets.get(key, set()).difference_update(members)

    async def hkeys(self, key):
        return list(self.hashes.get(key, {}))

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        pass

    def pipeline(self, transaction=True):
        redis = self

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
                    await getattr(redis, name)(*a, **k)

        return Pipe()


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(redis_module.redis_client, "redis", fake)
    return fake


@pytest.mark.asyncio
async def test_a_token_is_single_use(redis):
    t = await refresh_tokens.create_refresh_token("u1", "hash-1")
    claim = await refresh_tokens.claim_refresh_token(t)
    assert claim.user_id == "u1"
    assert claim.credential == refresh_tokens.credential_fingerprint("hash-1")
    assert await refresh_tokens.claim_refresh_token(t) is None


@pytest.mark.asyncio
async def test_revoke_all_ends_every_session_of_that_user_only(redis):
    a1 = await refresh_tokens.create_refresh_token("a", "h")
    a2 = await refresh_tokens.create_refresh_token("a", "h")
    b1 = await refresh_tokens.create_refresh_token("b", "h")

    assert await refresh_tokens.revoke_all_refresh_tokens("a") == 2

    assert await refresh_tokens.claim_refresh_token(a1) is None
    assert await refresh_tokens.claim_refresh_token(a2) is None
    assert (await refresh_tokens.claim_refresh_token(b1)).user_id == "b"


@pytest.mark.asyncio
async def test_a_pre_fingerprint_token_is_refused(redis):
    redis.kv["refresh:legacy"] = "some-user-id"
    assert await refresh_tokens.claim_refresh_token("legacy") is None


@pytest.mark.parametrize("update, revoked", [
    ({"password": "new-password-1"}, True),
    ({"is_active": False}, True),
    ({"is_active": True}, False),
    ({"email": "x@example.com"}, False),
])
@pytest.mark.asyncio
async def test_user_updates_that_end_sessions(redis, update, revoked):
    uid = str(uuid.uuid4())
    t = await refresh_tokens.create_refresh_token(uid, "h")
    manager = users.UserManager(user_db=None)
    await manager.on_after_update(SimpleNamespace(id=uid), update)
    assert (await refresh_tokens.claim_refresh_token(t) is None) is revoked


@pytest.mark.parametrize("hook", ["on_after_reset_password", "on_after_delete"])
@pytest.mark.asyncio
async def test_reset_and_delete_end_sessions(redis, hook):
    uid = str(uuid.uuid4())
    t = await refresh_tokens.create_refresh_token(uid, "h")
    await getattr(users.UserManager(user_db=None), hook)(SimpleNamespace(id=uid))
    assert await refresh_tokens.claim_refresh_token(t) is None


@pytest.fixture
def refresh_client(redis):
    user = SimpleNamespace(id=uuid.uuid4(), email="u@example.com", company_id=uuid.uuid4(),
                           role="engineer", is_active=True, is_superuser=False, hashed_password="hash-1")

    class Manager:
        async def get(self, user_id):
            assert user_id == user.id
            return user

    main.app.dependency_overrides[get_user_manager] = lambda: Manager()
    yield TestClient(main.app, raise_server_exceptions=False), user
    main.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_refresh_rotates(refresh_client):
    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    r = client.post("/auth/refresh", json={"refresh_token": t})
    assert r.status_code == 200
    assert r.json()["refresh_token"] != t
    assert client.post("/auth/refresh", json={"refresh_token": t}).status_code == 401


@pytest.mark.asyncio
async def test_password_set_outside_the_gateway_ends_the_sessions(refresh_client, redis):
    client, user = refresh_client
    t1 = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    t2 = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    user.hashed_password = "hash-2"  # e.g. scripts/manage_users.py reset-password

    r = client.post("/auth/refresh", json={"refresh_token": t1})
    assert r.status_code == 401
    # The other session is gone as well, not merely refused at its own next refresh.
    assert f"refresh:{t2}" not in redis.kv


@pytest.mark.asyncio
async def test_refresh_of_a_deactivated_user_is_refused(refresh_client):
    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    user.is_active = False
    assert client.post("/auth/refresh", json={"refresh_token": t}).status_code == 401


def test_stored_value_names_the_user_and_the_credential(redis):
    import asyncio

    t = asyncio.run(refresh_tokens.create_refresh_token("u9", "h9"))
    assert json.loads(redis.kv[f"refresh:{t}"]) == {
        "uid": "u9", "cred": refresh_tokens.credential_fingerprint("h9")}
    assert t in redis.sets["refresh-user:u9"]
    assert REFRESH_COOKIE == "if_refresh"


def _give_capability(redis, user_id, handle="h1"):
    # The hub's per-user capability index (capability_auth.revoke_user_capabilities reads it).
    redis.kv[f"nbcap:{handle}"] = "{}"
    redis.hashes[f"nbcap-user:{user_id}"] = {handle: "1"}


@pytest.mark.asyncio
async def test_password_change_also_ends_notebook_capabilities(redis):
    uid = str(uuid.uuid4())
    t = await refresh_tokens.create_refresh_token(uid, "h")
    _give_capability(redis, uid)
    await users.UserManager(user_db=None).on_after_update(SimpleNamespace(id=uid), {"password": "x"})
    assert await refresh_tokens.claim_refresh_token(t) is None
    assert "nbcap:h1" not in redis.kv


@pytest.mark.asyncio
async def test_logout_revokes_the_session_and_the_users_capabilities(refresh_client, redis):
    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    other = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    _give_capability(redis, user.id)
    assert client.post("/auth/logout", json={"refresh_token": t}).status_code == 204
    assert f"refresh:{t}" not in redis.kv
    assert t not in redis.sets.get(f"refresh-user:{user.id}", set())
    assert "nbcap:h1" not in redis.kv
    # Logout ends this session only; the user's other session stays.
    assert f"refresh:{other}" in redis.kv


@pytest.mark.asyncio
async def test_logout_with_a_pre_fingerprint_token_still_revokes_capabilities(refresh_client, redis):
    client, user = refresh_client
    redis.kv["refresh:legacy"] = str(user.id)
    _give_capability(redis, user.id)
    assert client.post("/auth/logout", json={"refresh_token": "legacy"}).status_code == 204
    assert "refresh:legacy" not in redis.kv and "nbcap:h1" not in redis.kv
