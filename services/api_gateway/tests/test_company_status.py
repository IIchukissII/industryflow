# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A suspended company's users are refused at authentication (ADR-0004 dec 2, ADR-0033 dec 3).

Sign-in, every session-authenticated request, refresh and capability-authenticated data requests
read the company's active flag, through a cache of bounded staleness rather than a database read
per request; and a handle bound to a suspended company does not resolve while the suspension
marker is in the capability store. The registry read is faked (conftest.company_flags); the store is
industryflow_contracts.testing's (a real Redis in CI).
"""
import json
import time
import uuid
from types import SimpleNamespace

import pytest

from fastapi import HTTPException
from fastapi_users import BaseUserManager
from industryflow_contracts import testing

import company_status
import dependencies
import users
from capability_auth import resolve_capability, suspended_key


async def _store():
    """The shared capability store, connected as messaging.redis_client connects."""
    return await testing.async_redis(encoding="utf-8", decode_responses=True)


@pytest.mark.asyncio
async def test_a_suspension_is_refused_at_once_by_every_process(company_flags, monkeypatch):
    # Another worker cached the company as active; the suspension's marker, written by the worker that
    # suspended it, refuses it here without waiting for the cache (ADR-0033 dec 3).
    cid = str(uuid.uuid4())
    store = await _store()
    monkeypatch.setattr(company_status, "_store", lambda: store)
    assert await company_status.is_company_active(cid) is True
    await company_status.mark_suspended(store, cid)
    assert await company_status.is_company_active(cid) is False
    await company_status.clear_suspended(store, cid)  # reactivated
    assert await company_status.is_company_active(cid) is True


@pytest.mark.asyncio
async def test_an_unreachable_store_leaves_the_decision_to_the_registry(company_flags, monkeypatch):
    cid = str(uuid.uuid4())
    store = await _store()

    async def unreachable(key):
        raise ConnectionError("store unreachable")

    monkeypatch.setattr(store, "exists", unreachable)
    monkeypatch.setattr(company_status, "_store", lambda: store)
    assert await company_status.is_company_active(cid) is True
    company_flags.inactive.add(cid)
    company_status.forget(cid)
    assert await company_status.is_company_active(cid) is False


@pytest.mark.asyncio
async def test_the_flag_is_cached_within_its_staleness_bound(company_flags, monkeypatch):
    cid = str(uuid.uuid4())
    assert await company_status.is_company_active(cid) is True
    company_flags.inactive.add(cid)
    assert await company_status.is_company_active(cid) is True  # still within the bound
    assert company_flags.reads == [cid]

    monkeypatch.setattr(company_status.get_settings(), "COMPANY_STATUS_CACHE_SECONDS", 0)
    company_status.clear()
    assert await company_status.is_company_active(cid) is False
    assert await company_status.is_company_active(cid) is False
    assert company_flags.reads == [cid, cid, cid]  # a zero bound reads every time


@pytest.mark.asyncio
async def test_expiry_rereads_the_registry(company_flags, monkeypatch):
    cid = str(uuid.uuid4())
    clock = [1000.0]
    monkeypatch.setattr(company_status.time, "monotonic", lambda: clock[0])
    assert await company_status.is_company_active(cid) is True
    company_flags.inactive.add(cid)
    clock[0] += company_status.get_settings().COMPANY_STATUS_CACHE_SECONDS + 1
    assert await company_status.is_company_active(cid) is False


@pytest.mark.asyncio
async def test_forget_makes_a_local_change_visible_at_once(company_flags):
    cid = str(uuid.uuid4())
    assert await company_status.is_company_active(cid) is True
    company_flags.inactive.add(cid)
    company_status.forget(cid)
    assert await company_status.is_company_active(cid) is False


@pytest.mark.asyncio
async def test_a_malformed_company_is_not_active(company_flags):
    assert await company_status.is_company_active("not-a-uuid") is False
    assert company_flags.reads == []


@pytest.mark.asyncio
async def test_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(company_status, "_CACHE_MAX_COMPANIES", 3)
    for _ in range(10):
        await company_status.is_company_active(uuid.uuid4())
    assert len(company_status._cache) == 3


@pytest.mark.parametrize("row, active", [(SimpleNamespace(is_active=True), True),
                                         (SimpleNamespace(is_active=False), False),
                                         (None, False)])
@pytest.mark.asyncio
async def test_the_registry_reader(company_flags, monkeypatch, row, active):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, clause, params):
            assert "public.companies" in str(clause)
            return SimpleNamespace(fetchone=lambda: row)

    monkeypatch.setattr(company_status, "_session_factory", Session)
    assert await company_flags.real_read(str(uuid.uuid4())) is active


# --- where authentication reads it -----------------------------------------------------------

def _user(company_id=None):
    return SimpleNamespace(id=uuid.uuid4(), company_id=company_id or uuid.uuid4(), is_active=True,
                           role="engineer", hashed_password="h")


class _Manager:
    def __init__(self, user):
        self.user = user

    def parse_id(self, value):
        return uuid.UUID(value)

    async def get(self, user_id):
        return self.user


@pytest.mark.asyncio
async def test_a_session_request_of_a_suspended_company_is_refused(company_flags, monkeypatch):
    from messaging import redis_client as redis_module

    monkeypatch.setattr(redis_module.redis_client, "redis", await _store())  # no session ended
    user = _user()
    strategy = users.get_jwt_strategy()
    token = await strategy.write_token(user)
    assert await strategy.read_token(token, _Manager(user)) is user
    company_flags.inactive.add(str(user.company_id))
    company_status.clear()
    assert await strategy.read_token(token, _Manager(user)) is None


@pytest.mark.asyncio
async def test_access_tokens_name_their_session_and_issue_time():
    import jwt as pyjwt

    before = int(time.time())
    token = await users.get_jwt_strategy().write_token(_user(), "sid-1")
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["sid"] == "sid-1" and claims["iat"] >= before


@pytest.mark.asyncio
async def test_a_suspended_companys_user_cannot_sign_in(company_flags, monkeypatch):
    user = _user()

    async def authenticate(self, credentials):
        return user

    from messaging import redis_client as redis_module

    monkeypatch.setattr(redis_module.redis_client, "redis", await _store())
    monkeypatch.setattr(BaseUserManager, "authenticate", authenticate)
    manager = users.UserManager(user_db=None)
    credentials = SimpleNamespace(username="u@example.com", password="pw")
    assert await manager.authenticate(credentials) is user
    company_flags.inactive.add(str(user.company_id))
    company_status.clear()
    assert await manager.authenticate(credentials) is None


@pytest.mark.asyncio
async def test_a_capability_of_a_suspended_company_is_refused_by_the_data_api(company_flags, monkeypatch):
    cid = str(uuid.uuid4())
    record = json.dumps({"user": "alice", "company_id": cid, "audience": "api", "read_only": True})

    async def get(key):
        return record if key == "nbcap:h" else None

    store = await _store()
    monkeypatch.setattr(dependencies, "_capability_get", get)
    monkeypatch.setattr(dependencies, "_capability_store", lambda: store)
    request = SimpleNamespace(headers={dependencies.CAPABILITY_HEADER: "h"})
    assert (await dependencies.resolve_tenant_context(request=request, user=None)).company_id == cid
    company_flags.inactive.add(cid)
    company_status.clear()
    with pytest.raises(HTTPException) as e:
        await dependencies.resolve_tenant_context(request=request, user=None)
    assert e.value.status_code == 401
    # The refusal is recorded with the tenant it named, never the handle (ADR-0042 dec 1-2).
    [refused] = await testing.audit_events(store, "refused")
    assert (refused["reason"], refused["company_id"], refused["plane"]) == ("tenant_inactive", cid, "api_gateway")
    assert "h" not in (await testing.audit_stream(store))[0].values()


@pytest.mark.asyncio
async def test_the_suspension_marker_denies_a_handle():
    cid = str(uuid.uuid4())
    kv = {"nbcap:h": json.dumps({"user": "u", "company_id": cid, "audience": "api"})}

    async def get(key):
        return kv.get(key)

    assert await resolve_capability(get, "h") is not None
    kv[suspended_key(cid)] = "1"
    assert await resolve_capability(get, "h") is None


@pytest.mark.asyncio
async def test_marking_and_lifting_a_suspension():
    store = await _store()
    cid = uuid.uuid4()
    await company_status.mark_suspended(store, cid)
    assert await store.keys("*") == [f"nbcap-suspended:{cid}"]
    assert await store.get(f"nbcap-suspended:{cid}") == "1"
    assert await store.ttl(f"nbcap-suspended:{cid}") == -1  # lasts as long as the suspension
    await company_status.clear_suspended(store, cid)
    assert await store.keys("*") == []


@pytest.mark.asyncio
async def test_a_hung_suspension_marker_read_gives_up_within_the_bound(monkeypatch):
    import asyncio

    from industryflow_contracts.sessions import READ_TIMEOUT_SECONDS

    store = await _store()

    async def hung(key):
        await asyncio.sleep(60)

    monkeypatch.setattr(store, "exists", hung)
    monkeypatch.setattr(company_status, "_store", lambda: store)
    monkeypatch.setattr(company_status, "READ_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    assert await company_status._marked_suspended(str(uuid.uuid4())) is False
    assert time.monotonic() - started < READ_TIMEOUT_SECONDS
