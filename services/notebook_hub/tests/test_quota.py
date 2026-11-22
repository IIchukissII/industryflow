# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's cap on notebook environments running at once (ADR-0049, ADR-0011 dec 7): counted from
the hub's own record of servers, across the tenant's users, from the override or the default."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import quota  # noqa: E402
from industryflow_contracts import quotas, testing  # noqa: E402

TENANT = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
OTHER = "11111111-2222-3333-4444-555555555555"


class User:
    def __init__(self, name, company_id, users):
        self.name, self.company_id, self.spawners = name, company_id, {}
        self.settings = {"users": users}

    async def get_auth_state(self):
        return {"company_id": self.company_id}


class Spawner:
    def __init__(self, user, name="", active=True, started_for=None, ready=None):
        self.user, self.name, self.active = user, name, active
        self.ready = active if ready is None else ready
        self._audit_company_id = started_for
        user.spawners[name] = self


def hub(*servers):
    """A hub's record: ``servers`` are ``(user name, tenant, active)``; returns the users dict."""
    users = {}
    for name, company_id, active in servers:
        user = users.get(name) or User(name, company_id, users)
        users[name] = user
        Spawner(user, name=f"s{len(user.spawners)}", active=active)
    return users


def asking(users, name, company_id):
    """The new server a user of ``company_id`` asks for."""
    user = users.get(name) or User(name, company_id, users)
    users[name] = user
    return Spawner(user, name="new", active=True, ready=False)


@pytest.fixture
def store():
    return testing.sync_redis(rebuilt=False)


def test_the_tenants_running_servers_are_counted_across_its_users_and_no_other_tenants(store, monkeypatch):
    monkeypatch.setattr(quota, "DEFAULT", 2)
    users = hub(("ann", TENANT, True), ("bob", TENANT, False), ("cyd", OTHER, True), ("cyd2", OTHER, True))
    asyncio.run(quota.admit(asking(users, "dee", TENANT), TENANT, store, store))  # 1 running < 2
    users = hub(("ann", TENANT, True), ("bob", TENANT, True), ("cyd", OTHER, True))
    with pytest.raises(quota.AtCap) as refused:
        asyncio.run(quota.admit(asking(users, "dee", TENANT), TENANT, store, store))
    assert refused.value.cap == 2 and "2 notebook environment" in str(refused.value)


def test_a_server_counts_for_the_tenant_it_was_started_for(store, monkeypatch):
    monkeypatch.setattr(quota, "DEFAULT", 1)
    users = {}
    moved = User("eve", OTHER, users)  # the login's tenant changed since the server started
    users["eve"] = moved
    Spawner(moved, active=True, started_for=TENANT)
    with pytest.raises(quota.AtCap):
        asyncio.run(quota.admit(asking(users, "fay", TENANT), TENANT, store, store))


def test_the_override_in_the_store_replaces_the_default_and_a_bad_one_does_not(store, monkeypatch):
    monkeypatch.setattr(quota, "DEFAULT", 1)
    users = hub(("ann", TENANT, True))
    store.set(quotas.cap_key("notebook_environments", TENANT), 3)
    asyncio.run(quota.admit(asking(users, "dee", TENANT), TENANT, store, store))
    store.set(quotas.cap_key("notebook_environments", TENANT), "zero")
    with pytest.raises(quota.AtCap):
        asyncio.run(quota.admit(asking(users, "dee", TENANT), TENANT, store, store))


def test_an_off_cap_admits_and_an_unreadable_override_takes_the_default(monkeypatch):
    class Down:
        def get(self, key):
            raise ConnectionError("store unreachable")

        def pipeline(self, transaction=True):
            raise ConnectionError("store unreachable")

    users = hub(("ann", TENANT, True), ("bob", TENANT, True))
    monkeypatch.setattr(quota, "DEFAULT", None)
    asyncio.run(quota.admit(asking(users, "dee", TENANT), TENANT, Down(), Down()))
    monkeypatch.setattr(quota, "DEFAULT", 2)
    with pytest.raises(quota.AtCap):
        asyncio.run(quota.admit(asking(users, "dee", TENANT), TENANT, Down(), Down()))


def test_a_refusal_is_recorded_against_the_tenant_and_in_the_tenantless_total(store, monkeypatch):
    monkeypatch.setattr(quota, "DEFAULT", 1)
    users = hub(("ann", TENANT, True))
    for _ in range(2):
        with pytest.raises(quota.AtCap):
            asyncio.run(quota.admit(asking(users, "dee", TENANT), TENANT, store, store))
    days = [k for k in store.keys("quota:refusals:notebook_environments:*")]
    assert len(days) == 1 and int(store.hget(days[0], TENANT)) == 2
    assert 0 < store.ttl(days[0]) <= quotas.REFUSALS_KEEP_DAYS * 86400
    lasts = list(store.keys("quota:refused-last:notebook_environments:*"))
    assert len(lasts) == 1 and store.hget(lasts[0], TENANT) is not None
    assert 0 < store.ttl(lasts[0]) <= quotas.REFUSALS_KEEP_DAYS * 86400
    assert int(store.get(quotas.refusals_total_key("notebook_environments"))) == 2


def test_a_start_still_waiting_on_its_check_holds_no_place(store, monkeypatch):
    """Two starts at the cap's edge: the one checked first is admitted, the other is refused, and a
    start not yet checked takes no place from either."""
    monkeypatch.setattr(quota, "DEFAULT", 3)
    users = hub(("ann", TENANT, True), ("bob", TENANT, True))
    first, second = asking(users, "cyd", TENANT), None
    second_user = User("dee", TENANT, users)
    users["dee"] = second_user
    second = Spawner(second_user, name="new", active=True, ready=False)  # pending, not yet checked
    asyncio.run(quota.admit(first, TENANT, store, store))
    assert first._quota_admitted is True
    with pytest.raises(quota.AtCap):
        asyncio.run(quota.admit(second, TENANT, store, store))
    assert second._quota_admitted is False


def test_concurrent_checks_admit_no_more_than_the_cap(store, monkeypatch):
    monkeypatch.setattr(quota, "DEFAULT", 3)
    users = hub(("ann", TENANT, True), ("bob", TENANT, True))
    starts = [asking(users, f"u{i}", TENANT) for i in range(4)]

    async def all_at_once():
        return await asyncio.gather(*(quota.admit(s, TENANT, store, store) for s in starts),
                                    return_exceptions=True)

    outcomes = asyncio.run(all_at_once())
    assert sum(o is None for o in outcomes) == 1
    assert all(isinstance(o, quota.AtCap) for o in outcomes if o is not None)
