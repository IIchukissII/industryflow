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

from capability_audit_fake import AuditScripts
from fastapi.testclient import TestClient

import main
import users
from messaging import redis_client as redis_module
from messaging import refresh_tokens
from routers.auth_tokens import REFRESH_COOKIE
from users import get_user_manager


class FakeRedis(AuditScripts):
    def __init__(self):
        self.kv, self.sets, self.hashes = {}, {}, {}

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)

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
    ({"company_id": uuid.uuid4()}, True),
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
    # Presented again within the grace (another tab): the successor already issued.
    again = client.post("/auth/refresh", json={"refresh_token": t})
    assert again.status_code == 200 and again.json()["refresh_token"] == r.json()["refresh_token"]


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
    stored = json.loads(redis.kv[f"refresh:{t}"])
    assert stored.pop("sid")
    assert stored == {"uid": "u9", "cred": refresh_tokens.credential_fingerprint("h9")}
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


@pytest.mark.asyncio
async def test_refresh_of_a_suspended_companys_user_is_refused_and_ends_its_sessions(refresh_client, redis, company_flags):
    import company_status

    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    other = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password)
    company_flags.inactive.add(str(user.company_id))
    company_status.clear()
    assert client.post("/auth/refresh", json={"refresh_token": t}).status_code == 401
    assert f"refresh:{other}" not in redis.kv


@pytest.mark.asyncio
async def test_rotation_keeps_the_session_id(refresh_client, redis):
    import jwt as pyjwt

    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-a")
    r = client.post("/auth/refresh", json={"refresh_token": t})
    assert r.status_code == 200
    assert json.loads(redis.kv[f"refresh:{r.json()['refresh_token']}"])["sid"] == "sid-a"
    assert pyjwt.decode(r.json()["access_token"], options={"verify_signature": False})["sid"] == "sid-a"


@pytest.mark.asyncio
async def test_logout_records_that_its_session_ended(refresh_client, redis):
    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-a")
    await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-b")
    assert client.post("/auth/logout", json={"refresh_token": t}).status_code == 204
    assert await refresh_tokens.session_ended(str(user.id), "sid-a", None) is True
    assert await refresh_tokens.session_ended(str(user.id), "sid-b", 0) is False


@pytest.mark.asyncio
async def test_ending_all_sessions_records_each_session_and_the_time(redis):
    import time

    uid = str(uuid.uuid4())
    await refresh_tokens.create_refresh_token(uid, "h", "sid-a")
    await refresh_tokens.end_all_sessions(uid)
    assert await refresh_tokens.session_ended(uid, "sid-a", time.time() + 60) is True  # by its id
    assert await refresh_tokens.session_ended(uid, "sid-z", time.time() - 60) is True  # by its time
    assert await refresh_tokens.session_ended(uid, None, None) is True
    assert await refresh_tokens.session_ended(uid, "sid-z", time.time() + 60) is False
    assert await refresh_tokens.session_ended(str(uuid.uuid4()), None, None) is False


# --- every request reads it (ADR-0004 dec 2) -------------------------------------------------

class _Manager:
    def __init__(self, user):
        self.user = user

    def parse_id(self, value):
        return uuid.UUID(value)

    async def get(self, user_id):
        return self.user


def _account():
    return SimpleNamespace(id=uuid.uuid4(), company_id=None, is_active=True, role="engineer",
                           hashed_password="h")


@pytest.mark.asyncio
async def test_an_access_token_stops_working_when_its_session_is_logged_out(refresh_client, redis):
    client, user = refresh_client
    strategy = users.get_jwt_strategy()
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-a")
    this, other = await strategy.write_token(user, "sid-a"), await strategy.write_token(user, "sid-b")
    assert await strategy.read_token(this, _Manager(user)) is user
    assert client.post("/auth/logout", json={"refresh_token": t}).status_code == 204
    assert await strategy.read_token(this, _Manager(user)) is None
    assert await strategy.read_token(other, _Manager(user)) is user


@pytest.mark.asyncio
async def test_ending_all_sessions_refuses_the_access_tokens_issued_before_it(redis, monkeypatch):
    import time

    user = _account()
    strategy = users.get_jwt_strategy()
    real = time.time
    clock = [real() - 10]  # never ahead of the real clock: a token issued in the future is refused
    monkeypatch.setattr(time, "time", lambda: clock[0])
    before = await strategy.write_token(user, "sid-a")
    clock[0] += 5
    await refresh_tokens.end_all_sessions(str(user.id))
    clock[0] += 5
    after = await strategy.write_token(user, "sid-b")
    monkeypatch.setattr(time, "time", real)
    assert await strategy.read_token(before, _Manager(user)) is None
    assert await strategy.read_token(after, _Manager(user)) is user


@pytest.mark.asyncio
async def test_an_unreadable_session_store_refuses_the_request(redis, monkeypatch):
    from fastapi import HTTPException

    user = _account()
    strategy = users.get_jwt_strategy()
    token = await strategy.write_token(user, "sid-a")

    async def down(key):
        raise ConnectionError("down")

    monkeypatch.setattr(redis, "get", down)
    with pytest.raises(HTTPException) as refused:
        await strategy.read_token(token, _Manager(user))
    assert refused.value.status_code == 503


@pytest.mark.asyncio
async def test_every_access_token_names_a_session():
    import jwt as pyjwt

    token = await users.get_jwt_strategy().write_token(_account())
    assert pyjwt.decode(token, options={"verify_signature": False})["sid"]


@pytest.mark.asyncio
async def test_the_fastapi_users_logout_ends_its_session_and_the_capabilities(redis):
    user = _account()
    strategy = users.get_jwt_strategy()
    token, other = await strategy.write_token(user), await strategy.write_token(user)
    _give_capability(redis, user.id)
    await strategy.destroy_token(token, user)
    assert await strategy.read_token(token, _Manager(user)) is None
    assert await strategy.read_token(other, _Manager(user)) is user
    assert "nbcap:h1" not in redis.kv


@pytest.mark.asyncio
async def test_a_logout_with_only_an_access_token_ends_its_session(refresh_client, redis):
    client, user = refresh_client
    strategy = users.get_jwt_strategy()
    token = await strategy.write_token(user, "sid-bearer")
    _give_capability(redis, user.id)
    assert client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert await refresh_tokens.session_ended(str(user.id), "sid-bearer", None) is True
    assert "nbcap:h1" not in redis.kv


@pytest.mark.asyncio
async def test_a_logout_with_a_forged_access_token_ends_nothing(refresh_client, redis):
    import jwt as pyjwt

    client, user = refresh_client
    forged = pyjwt.encode({"sub": str(user.id), "sid": "sid-victim", "aud": ["fastapi-users:auth"]},
                          "not-the-secret", algorithm="HS256")
    assert client.post("/auth/logout", headers={"Authorization": f"Bearer {forged}"}).status_code == 204
    assert await refresh_tokens.session_ended(str(user.id), "sid-victim", None) is False


@pytest.mark.asyncio
async def test_a_token_minted_right_after_an_end_of_all_sessions_is_not_dead(redis):
    """Within the clock-skew allowance a token would count as issued before the end, for its whole
    life; the mint waits the allowance out instead."""
    import time

    from industryflow_contracts import sessions

    user = _account()
    strategy = users.get_jwt_strategy()
    await refresh_tokens.end_all_sessions(str(user.id))
    started = time.monotonic()
    token = await strategy.write_token(user, "sid-new")
    waited = time.monotonic() - started
    assert await strategy.read_token(token, _Manager(user)) is user
    assert 0 < waited <= sessions.CLOCK_SKEW_SECONDS + 1.5


@pytest.mark.asyncio
async def test_no_wait_without_a_recent_end(redis):
    import time

    user = _account()
    redis.kv[f"sessions-ended-at:{user.id}"] = repr(time.time() - 60)
    assert await refresh_tokens.wait_out_an_end(str(user.id)) == 0.0
    assert await refresh_tokens.wait_out_an_end(str(uuid.uuid4())) == 0.0



# --- reuse of a rotated refresh token (ADR-0004 dec 2) -----------------------------------------

def _age_rotation(redis, token, seconds):
    record = json.loads(redis.kv[f"refresh-rotated:{token}"])
    record["at"] -= seconds
    redis.kv[f"refresh-rotated:{token}"] = json.dumps(record)


@pytest.mark.asyncio
async def test_a_reuse_after_the_grace_ends_the_session(refresh_client, redis):
    client, user = refresh_client
    strategy = users.get_jwt_strategy()
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-r")
    other = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-other")
    rotated = client.post("/auth/refresh", json={"refresh_token": t}).json()
    _give_capability(redis, user.id)
    _age_rotation(redis, t, refresh_tokens.ROTATION_GRACE_SECONDS + 1)
    r = client.post("/auth/refresh", json={"refresh_token": t})
    assert r.status_code == 401 and "reused" in r.text
    # The thief's or the owner's copy: neither refreshes, and the session's access tokens are refused.
    assert client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 401
    assert await strategy.read_token(rotated["access_token"], _Manager(user)) is None
    assert "nbcap:h1" not in redis.kv
    # Another session of the user is untouched.
    assert f"refresh:{other}" in redis.kv


@pytest.mark.asyncio
async def test_within_the_grace_a_successor_already_rotated_again_is_not_given(refresh_client, redis):
    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-g")
    first = client.post("/auth/refresh", json={"refresh_token": t}).json()
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 200
    r = client.post("/auth/refresh", json={"refresh_token": t})
    assert r.status_code == 401 and "reused" not in r.text


@pytest.mark.asyncio
async def test_a_token_never_issued_is_simply_refused(refresh_client, redis):
    client, _ = refresh_client
    r = client.post("/auth/refresh", json={"refresh_token": "never-issued"})
    assert r.status_code == 401 and "reused" not in r.text



@pytest.mark.asyncio
async def test_a_refresh_token_issued_in_a_race_with_the_end_does_not_continue_the_session(refresh_client, redis):
    client, user = refresh_client
    late = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-race")
    await refresh_tokens._end_reused_session(refresh_tokens.RefreshClaim(str(user.id), "c", "sid-race"))
    # Missed by the end's scan (minted after it): still refused, for the refresh lifetime.
    redis.kv[f"refresh:{late}"] = json.dumps({"uid": str(user.id), "cred": refresh_tokens.credential_fingerprint(user.hashed_password), "sid": "sid-race"})
    assert client.post("/auth/refresh", json={"refresh_token": late}).status_code == 401


@pytest.mark.asyncio
async def test_only_the_first_reuse_revokes_the_capabilities(refresh_client, redis):
    client, user = refresh_client
    t = await refresh_tokens.create_refresh_token(str(user.id), user.hashed_password, "sid-once")
    client.post("/auth/refresh", json={"refresh_token": t})
    _age_rotation(redis, t, refresh_tokens.ROTATION_GRACE_SECONDS + 1)
    assert client.post("/auth/refresh", json={"refresh_token": t}).status_code == 401
    _give_capability(redis, user.id)
    assert client.post("/auth/refresh", json={"refresh_token": t}).status_code == 401
    assert "nbcap:h1" in redis.kv, "a repeat of an old token must not keep revoking the user's notebooks"


@pytest.mark.asyncio
async def test_a_refresh_racing_the_rotation_waits_for_its_successor(redis, monkeypatch):
    import asyncio

    t = await refresh_tokens.create_refresh_token("u-race", "h", "sid-wait")
    claim = await refresh_tokens.claim_refresh_token(t)
    assert claim is not None

    async def rotate_late():
        await asyncio.sleep(0.3)
        await refresh_tokens.record_successor(t, await refresh_tokens.create_refresh_token("u-race", "h", "sid-wait"))

    replayed, _ = await asyncio.gather(refresh_tokens.replay(t), rotate_late())
    assert replayed.kind == "successor" and replayed.successor


@pytest.mark.asyncio
async def test_a_refresh_racing_the_claim_waits_for_its_record(redis, monkeypatch):
    import asyncio

    t = await refresh_tokens.create_refresh_token("u-race2", "h", "sid-wait2")
    raw = redis.kv.pop(f"refresh:{t}")  # claimed, record not yet written

    async def record_late():
        await asyncio.sleep(0.2)
        redis.kv[f"refresh:{t}"] = raw
        await refresh_tokens.claim_refresh_token(t)
        await refresh_tokens.record_successor(t, await refresh_tokens.create_refresh_token("u-race2", "h", "sid-wait2"))

    replayed, _ = await asyncio.gather(refresh_tokens.replay(t), record_late())
    assert replayed.kind == "successor"
