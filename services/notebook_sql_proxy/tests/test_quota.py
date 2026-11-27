# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's SQL sessions are counted across replicas, under its cap and every tenant's total
(ADR-0049 dec 4, 8, 9, 10), on the store the proxy runs on (industryflow_contracts.testing)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import binding as b  # noqa: E402
import proxy  # noqa: E402
import quota  # noqa: E402
from industryflow_contracts import quotas, testing  # noqa: E402
from quota import AtCap, SessionQuota  # noqa: E402

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
B = "7d1c1f3e-2a41-4f0c-9d5e-8a51f1b6f0aa"
DEFAULTS = {quotas.SQL_SESSIONS: 2, quotas.SQL_STATEMENT_SECONDS: 300, quotas.SQL_IDLE_TRANSACTION_SECONDS: 60}


async def _quota(total=10):
    store = await testing.async_redis()
    return store, SessionQuota(store, store, DEFAULTS, total)


@pytest.mark.asyncio
async def test_a_tenant_at_its_cap_is_refused_naming_it_and_the_refusal_is_counted():
    store, q = await _quota()
    await q.open(A, 2, now=1000)
    await q.open(A, 2, now=1000)
    with pytest.raises(AtCap, match=r"2 SQL session\(s\) open, its limit \(sql_sessions\)") as refused:
        await q.open(A, 2, now=1000)
    assert refused.value.cap == quotas.SQL_SESSIONS
    # Another tenant is not held by A's sessions.
    await q.open(B, 2, now=1000)
    day = "19700101"
    assert int(await store.hget(quotas.refusals_key(quotas.SQL_SESSIONS, day), A)) == 1
    assert int(await store.get(quotas.refusals_total_key(quotas.SQL_SESSIONS))) == 1


@pytest.mark.asyncio
async def test_two_proxies_count_in_one_place():
    """Two replicas (two quotas over the one store) share the tenant's cap: the second is refused."""
    store = await testing.async_redis()
    first, second = SessionQuota(store, store, DEFAULTS, 10), SessionQuota(store, store, DEFAULTS, 10)
    await first.open(A, 1, now=1000)
    with pytest.raises(AtCap):
        await second.open(A, 1, now=1000)


@pytest.mark.asyncio
async def test_every_tenant_together_stays_within_the_total():
    store, q = await _quota(total=3)
    await q.open(A, 2, now=1000)
    await q.open(A, 2, now=1000)
    await q.open(B, 2, now=1000)
    with pytest.raises(AtCap, match="NOTEBOOK_SQL_SESSIONS_TOTAL=3") as refused:
        await q.open(B, 2, now=1000)
    assert refused.value.cap == quotas.SQL_SESSIONS_TOTAL_ENV
    assert int(await store.get(quotas.SQL_SESSIONS_TOTAL_REFUSALS_KEY)) == 1
    # A refusal at the total takes no place from the tenant's own count.
    assert await store.zcard(quotas.sql_sessions_key(B)) == 1


@pytest.mark.asyncio
async def test_a_released_lease_frees_its_place_and_a_lapsed_one_leaves_the_count():
    store, q = await _quota()
    lease = await q.open(A, 1, now=1000)
    await lease.release()
    stale = await q.open(A, 1, now=1000)          # held by a proxy that then stopped: never released
    assert stale is not None
    with pytest.raises(AtCap):
        await q.open(A, 1, now=1000 + quota.LEASE_SECONDS - 1)
    await q.open(A, 1, now=1000 + quota.LEASE_SECONDS + 1)   # its lease ran out


@pytest.mark.asyncio
async def test_a_renewed_lease_keeps_its_place():
    store, q = await _quota()
    lease = await q.open(A, 1, now=1000)
    await lease.renew(now=1000 + quota.LEASE_SECONDS - 5)
    with pytest.raises(AtCap):
        await q.open(A, 1, now=1000 + quota.LEASE_SECONDS + 1)


@pytest.mark.asyncio
async def test_an_off_cap_admits_within_the_total():
    _store, q = await _quota(total=2)
    await q.open(A, None, now=1000)
    await q.open(A, None, now=1000)
    with pytest.raises(AtCap) as refused:
        await q.open(A, None, now=1000)
    assert refused.value.cap == quotas.SQL_SESSIONS_TOTAL_ENV


class _Down:
    async def evalsha(self, *a, **k):
        raise ConnectionError("quota store down")

    eval = evalsha


@pytest.mark.asyncio
async def test_an_unreadable_count_admits_and_is_counted_where_the_gateway_reads_it():
    capabilities = await testing.async_redis()
    q = SessionQuota(capabilities, _Down(), DEFAULTS, 10)
    for _ in range(3):
        lease = await q.open(A, 1, now=1000)
        await lease.renew()
        await lease.release()
    assert int(await capabilities.get(quotas.count_unavailable_key(quotas.SQL_SESSIONS))) == 3


@pytest.mark.asyncio
async def test_the_limits_are_the_overrides_copied_for_the_proxy_else_the_defaults():
    store, q = await _quota()
    await store.set(quotas.cap_key(quotas.SQL_STATEMENT_SECONDS, A), 900)
    await store.set(quotas.cap_key(quotas.SQL_SESSIONS, A), "not a number")
    limits = await q.limits(A)
    assert limits == quota.Limits(sessions=2, statement_seconds=900, idle_transaction_seconds=60)
    assert await q.limits(B) == quota.Limits(2, 300, 60)


def test_the_settings_refuse_a_value_the_contract_cannot_read():
    defaults, total = quota.settings({"TENANT_QUOTA_SQL_SESSIONS": "off", "NOTEBOOK_SQL_SESSIONS_TOTAL": "40"})
    assert defaults[quotas.SQL_SESSIONS] is None and total == 40
    with pytest.raises(ValueError, match="NOTEBOOK_SQL_SESSIONS_TOTAL"):
        quota.settings({"NOTEBOOK_SQL_SESSIONS_TOTAL": "off"})


class _Backend:
    def __init__(self):
        self.relayed = None

    async def connect(self):
        pass

    async def execute(self, sql):
        pass

    async def relay(self, still_authorized, **kwargs):
        self.relayed = kwargs
        assert await still_authorized()

    async def close(self):
        pass


async def _bound_handle(store, company_id=A):
    """A valid SQL handle of ``company_id`` in the capability store."""
    await store.set(f"{b._KEY_PREFIX}good", json.dumps(
        {"user": "alice", "company_id": company_id, "audience": "sql", "read_only": True}))
    return "good"


@pytest.mark.asyncio
async def test_a_session_over_its_cap_opens_no_backend_and_one_under_it_is_timed():
    store = await testing.async_redis()
    q = SessionQuota(store, store, {**DEFAULTS, quotas.SQL_SESSIONS: 1}, 10)
    handle = await _bound_handle(store)
    held = _Backend()
    assert await proxy.serve_connection(handle, store, lambda b: held, quota=q) is True
    assert held.relayed["limits"] == quota.Limits(1, 300, 60) and held.relayed["refused"] is not None
    # The session above ended and released its place: the next is admitted; one held open is not.
    await q.open(A, 1)
    opened = []
    with pytest.raises(proxy.SessionRefused, match="sql_sessions"):
        await proxy.serve_connection(handle, store, lambda b: opened.append(b) or _Backend(), quota=q)
    assert opened == []
