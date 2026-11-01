# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Password guessing is slowed per account, never locked out (ADR-0004 dec 10)."""
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


@pytest_asyncio.fixture
async def store(monkeypatch):
    client = await testing.async_redis(decode_responses=True)
    monkeypatch.setattr(redis_module.redis_client, "redis", client)
    yield client
    await client.aclose()


def test_the_wait_doubles_from_the_threshold_and_is_capped():
    assert [throttle.delay_after(n) for n in range(throttle.THRESHOLD)] == [0] * throttle.THRESHOLD
    assert [throttle.delay_after(throttle.THRESHOLD + i) for i in range(4)] == [1, 2, 4, 8]
    assert throttle.delay_after(throttle.THRESHOLD + 40) == throttle.MAX_DELAY_SECONDS


@pytest.mark.asyncio
async def test_failures_below_the_threshold_cost_nothing_and_beyond_it_wait(store):
    for _ in range(throttle.THRESHOLD - 1):
        await throttle.failed(store, EMAIL)
    assert await throttle.retry_after(store, EMAIL) == 0
    for _ in range(4):
        await throttle.failed(store, EMAIL)
    assert 1 <= await throttle.retry_after(store, EMAIL) <= throttle.delay_after(throttle.THRESHOLD + 3)
    # The address as submitted is normalised; another address is untouched.
    assert await throttle.retry_after(store, "  VICTIM@example.com ") > 0
    assert await throttle.retry_after(store, "other@example.com") == 0
    assert 0 < await store.ttl(throttle._key(EMAIL)) <= throttle.FORGET_AFTER_SECONDS


@pytest.mark.asyncio
async def test_clearing_forgets_the_failures(store):
    for _ in range(throttle.THRESHOLD + 3):
        await throttle.failed(store, EMAIL)
    await throttle.cleared(store, EMAIL)
    assert await throttle.retry_after(store, EMAIL) == 0


def _manager(monkeypatch, outcome):
    async def authenticate(self, credentials):
        return outcome(credentials)

    monkeypatch.setattr(BaseUserManager, "authenticate", authenticate)
    return users.UserManager(user_db=None)


@pytest.mark.asyncio
async def test_inside_the_wait_the_password_is_not_checked(store, monkeypatch):
    checked = []
    account = SimpleNamespace(id="u", company_id=None)
    manager = _manager(monkeypatch, lambda c: checked.append(c.password) or (account if c.password == "right" else None))
    for _ in range(throttle.THRESHOLD + 2):
        assert await manager.authenticate(SimpleNamespace(username=EMAIL, password="wrong")) is None
        await store.hset(throttle._key(EMAIL), "at", repr(time.time() - 3600))  # every wait served
    await store.hset(throttle._key(EMAIL), "at", repr(time.time()))
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
    assert await throttle.retry_after(store, "nobody@example.com") > 0
    with pytest.raises(HTTPException) as refused:
        await manager.authenticate(SimpleNamespace(username="nobody@example.com", password="x"))
    assert refused.value.status_code == 429


@pytest.mark.asyncio
async def test_a_success_clears_the_count(store, monkeypatch):
    account = SimpleNamespace(id="u", company_id=None)
    manager = _manager(monkeypatch, lambda c: account if c.password == "right" else None)
    for _ in range(throttle.THRESHOLD - 1):
        await manager.authenticate(SimpleNamespace(username=EMAIL, password="wrong"))
    assert await manager.authenticate(SimpleNamespace(username=EMAIL, password="right")) is account
    assert await store.exists(throttle._key(EMAIL)) == 0


@pytest.mark.asyncio
async def test_a_password_change_clears_the_count(store):
    for _ in range(throttle.THRESHOLD + 3):
        await throttle.failed(store, EMAIL)
    await users._clear_sign_in_failures(SimpleNamespace(email=EMAIL))
    assert await throttle.retry_after(store, EMAIL) == 0


@pytest.mark.asyncio
async def test_an_unreadable_store_refuses_the_sign_in(monkeypatch):
    class Down:
        async def hgetall(self, key):
            raise ConnectionError("down")

    monkeypatch.setattr(redis_module.redis_client, "redis", Down())
    manager = _manager(monkeypatch, lambda c: SimpleNamespace(id="u", company_id=None))
    with pytest.raises(HTTPException) as refused:
        await manager.authenticate(SimpleNamespace(username=EMAIL, password="right"))
    assert refused.value.status_code == 503
