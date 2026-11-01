# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Password guessing is slowed per account and source, never locked out (ADR-0004 dec 10).

The store is industryflow_contracts.testing's (a real Redis in CI): the reservation is a Lua script."""
import asyncio
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi_users import BaseUserManager

import sign_in_throttle as throttle
import users
from industryflow_contracts import testing
from messaging import redis_client as redis_module

EMAIL = "victim@example.com"
ATTACKER, OWNER = "198.51.100.7", "203.0.113.9"


@pytest_asyncio.fixture
async def store(monkeypatch):
    client = await testing.async_redis(decode_responses=True)
    monkeypatch.setattr(redis_module.redis_client, "redis", client)
    yield client
    await client.aclose()


async def _serve_waits(store, email=EMAIL, source=ATTACKER):
    """As if every wait so far had been waited out."""
    await store.hset(throttle._key(email), f"at:{source}", repr(time.time() - 3600))


def test_the_wait_doubles_from_the_threshold_and_is_capped():
    assert [throttle.delay_after(n) for n in range(throttle.THRESHOLD)] == [0] * throttle.THRESHOLD
    assert [throttle.delay_after(throttle.THRESHOLD + i) for i in range(4)] == [1, 2, 4, 8]
    assert throttle.delay_after(throttle.THRESHOLD + 40) == throttle.MAX_DELAY_SECONDS


@pytest.mark.asyncio
async def test_below_the_threshold_nothing_waits_and_beyond_it_the_source_waits(store):
    for _ in range(throttle.THRESHOLD):
        assert await throttle.reserve(store, EMAIL, ATTACKER) == 0
    assert await throttle.reserve(store, EMAIL, ATTACKER) >= 1
    # The address as submitted is normalised; another address is untouched.
    assert await throttle.reserve(store, "  VICTIM@example.com ", ATTACKER) >= 1
    assert await throttle.reserve(store, "other@example.com", ATTACKER) == 0
    assert 0 < await store.ttl(throttle._key(EMAIL)) <= throttle.FORGET_AFTER_SECONDS


@pytest.mark.asyncio
async def test_a_refused_attempt_is_not_counted(store):
    for _ in range(throttle.THRESHOLD):
        await throttle.reserve(store, EMAIL, ATTACKER)
    for _ in range(20):
        assert await throttle.reserve(store, EMAIL, ATTACKER) >= 1
    assert int(await store.hget(throttle._key(EMAIL), f"n:{ATTACKER}")) == throttle.THRESHOLD


@pytest.mark.asyncio
async def test_guesses_sent_at_once_are_refused_all_but_one(store):
    for _ in range(throttle.THRESHOLD):
        await throttle.reserve(store, EMAIL, ATTACKER)
    await _serve_waits(store)
    waits = await asyncio.gather(*(throttle.reserve(store, EMAIL, ATTACKER) for _ in range(20)))
    assert waits.count(0) == 1


@pytest.mark.asyncio
async def test_one_source_slowing_an_account_does_not_slow_its_owner(store):
    """The lockout an account-wide count allows: one wrong password from the attacker every few
    minutes would keep the owner refused for ever."""
    for _ in range(throttle.THRESHOLD + 10):
        await throttle.reserve(store, EMAIL, ATTACKER)
        await _serve_waits(store)
    assert await throttle.reserve(store, EMAIL, OWNER) == 0


@pytest.mark.asyncio
async def test_a_success_clears_its_source_and_a_new_password_every_source(store):
    for source in (ATTACKER, OWNER):
        for _ in range(throttle.THRESHOLD + 2):
            await throttle.reserve(store, EMAIL, source)
            await _serve_waits(store, source=source)
    await throttle.succeeded(store, EMAIL, OWNER)
    assert await store.hget(throttle._key(EMAIL), f"n:{OWNER}") is None
    assert await store.hget(throttle._key(EMAIL), f"n:{ATTACKER}") is not None
    await users._clear_sign_in_failures(SimpleNamespace(email=EMAIL))
    assert await store.exists(throttle._key(EMAIL)) == 0


def _manager(monkeypatch, outcome, source=ATTACKER):
    async def authenticate(self, credentials):
        return outcome(credentials)

    monkeypatch.setattr(BaseUserManager, "authenticate", authenticate)
    manager = users.UserManager(user_db=None)
    manager.sign_in_source = source
    return manager


def _account(is_active=True):
    return SimpleNamespace(id="u", company_id=None, is_active=is_active)


@pytest.mark.asyncio
async def test_inside_the_wait_the_password_is_not_checked(store, monkeypatch):
    checked = []
    account = _account()
    manager = _manager(monkeypatch, lambda c: checked.append(c.password) or (account if c.password == "right" else None))
    for _ in range(throttle.THRESHOLD):
        assert await manager.authenticate(SimpleNamespace(username=EMAIL, password="wrong")) is None
    before = len(checked)
    with pytest.raises(HTTPException) as refused:
        await manager.authenticate(SimpleNamespace(username=EMAIL, password="right"))
    assert refused.value.status_code == 429 and int(refused.value.headers["Retry-After"]) >= 1
    assert len(checked) == before


@pytest.mark.asyncio
async def test_an_unknown_address_is_counted_like_a_known_one(store, monkeypatch):
    manager = _manager(monkeypatch, lambda c: None)
    for _ in range(throttle.THRESHOLD):
        await manager.authenticate(SimpleNamespace(username="nobody@example.com", password="x"))
    with pytest.raises(HTTPException) as refused:
        await manager.authenticate(SimpleNamespace(username="nobody@example.com", password="x"))
    assert refused.value.status_code == 429


@pytest.mark.asyncio
async def test_an_admitted_sign_in_clears_its_count_and_a_refused_one_does_not(store, monkeypatch):
    account = _account()
    manager = _manager(monkeypatch, lambda c: account if c.password == "right" else None)
    for _ in range(throttle.THRESHOLD - 1):
        await manager.authenticate(SimpleNamespace(username=EMAIL, password="wrong"))
    assert await manager.authenticate(SimpleNamespace(username=EMAIL, password="right")) is account
    assert await store.hget(throttle._key(EMAIL), f"n:{ATTACKER}") is None
    # An inactive account's right password is refused like a wrong one, and counted like one: the
    # count does not tell the two apart.
    inactive = _manager(monkeypatch, lambda c: _account(is_active=False) if c.password == "right" else None)
    await inactive.authenticate(SimpleNamespace(username=EMAIL, password="right"))
    assert int(await store.hget(throttle._key(EMAIL), f"n:{ATTACKER}")) == 1


@pytest.mark.asyncio
async def test_an_unreadable_store_refuses_the_sign_in(monkeypatch):
    class Down:
        async def eval(self, *args):
            raise ConnectionError("down")

    monkeypatch.setattr(redis_module.redis_client, "redis", Down())
    manager = _manager(monkeypatch, lambda c: _account())
    with pytest.raises(HTTPException) as refused:
        await manager.authenticate(SimpleNamespace(username=EMAIL, password="right"))
    assert refused.value.status_code == 503


@pytest.mark.asyncio
async def test_the_source_is_the_edges_report_only_from_the_edge(monkeypatch):
    import dependencies
    from industryflow_contracts import capability_audit as audit

    monkeypatch.setattr(dependencies, "EDGE_PEERS", audit.EdgePeers("10.0.0.5"))

    async def manager_for(peer, reported):
        connection = SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-real-ip": reported})
        gen = users.get_user_manager(connection, user_db=None)
        return await gen.__anext__()

    assert (await manager_for("10.0.0.5", OWNER)).sign_in_source == OWNER
    assert (await manager_for("10.9.9.9", OWNER)).sign_in_source == "10.9.9.9"
