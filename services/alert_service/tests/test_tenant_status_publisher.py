# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The worker publishes every registered tenant's status for ingestion (ADR-0033 dec 3).

Ingestion refuses a suspended tenant's devices only from this stream, so a tenant the publisher
leaves out, or a status it inverts, keeps those devices ingesting with nothing failing.
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import tenant_status_publisher as publisher  # noqa: E402
from industryflow_contracts import tenancy, tenant_status, testing  # noqa: E402

ACTIVE = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
SUSPENDED = "11111111-2222-3333-4444-555555555555"


class Pool:
    """Answers the registry read with ``rows`` and the overrides read with ``overrides``; ``fail``
    fails every read, ``fail_overrides`` only the overrides."""

    def __init__(self, rows, fail=False, overrides=(), fail_overrides=False, usage=(), fail_usage=False):
        self.rows, self.fail, self.queries = rows, fail, []
        self.overrides, self.fail_overrides = list(overrides), fail_overrides
        self.usage, self.fail_usage = list(usage), fail_usage

    @asynccontextmanager
    async def acquire(self):
        pool = self

        class Conn:
            async def fetch(self, sql, *args):
                pool.queries.append((sql, args))
                if pool.fail:
                    raise ConnectionError("database unreachable")
                if "tenant_quota_overrides" in sql:
                    if pool.fail_overrides:
                        raise ConnectionError("relation does not exist")
                    return pool.overrides
                if "tenant_storage_usage" in sql:
                    if pool.fail_usage:
                        raise ConnectionError("relation does not exist")
                    return pool.usage
                return pool.rows

        yield Conn()


def _rows():
    return [
        {"schema_name": tenancy.schema_name(ACTIVE), "is_active": True},
        {"schema_name": tenancy.schema_name(SUSPENDED), "is_active": False},
        {"schema_name": "tenant_registry", "is_active": False},  # not exactly a tenant's
    ]


def test_every_tenant_goes_out_with_its_status():
    sent = []

    async def send(topic, value, key):
        sent.append((topic, key, tenant_status.decode(value)))

    count = asyncio.run(publisher.publish_once(Pool(_rows()), send, "tenant-status"))
    assert count == 2
    assert sent == [
        ("tenant-status", ACTIVE.encode(), (ACTIVE, True)),
        ("tenant-status", SUSPENDED.encode(), (SUSPENDED, False)),
    ]


def test_the_registry_is_read_with_the_bound_tenant_pattern():
    pool = Pool([])
    asyncio.run(publisher.publish_once(pool, None, "tenant-status"))
    (sql, args), (overrides_sql, _), (usage_sql, _) = pool.queries
    assert overrides_sql == publisher.OVERRIDES_SELECT
    assert usage_sql == publisher.USAGE_SELECT
    assert "FROM public.companies" in sql and "is_active" in sql
    assert args == (tenancy.SCHEMA_LIKE,)


def test_a_failed_round_is_retried_on_the_next_tick(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(publisher.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(publisher.run(Pool([], fail=True), None, "tenant-status", 10))
    assert sleeps == [10, 10]


class DownStore:
    """A store that cannot be reached: every pipeline fails."""

    def pipeline(self, transaction=True):
        raise ConnectionError("store unreachable")


def _send_into(sent):
    """Collects each status as ``(company_id, active, caps)``; with ``over``, its over caps too."""
    async def send(topic, value, key):
        sent.append(tenant_status.decode_message(value)[:3])
    return send


def _send_with_over(sent):
    async def send(topic, value, key):
        sent.append(tenant_status.decode_message(value))
    return send


def test_a_tenants_overrides_ride_its_status_and_are_copied_for_the_hub():
    sent = []
    overrides = [{"company_id": ACTIVE, "cap": "readings_per_second", "value": 40},
                 {"company_id": ACTIVE, "cap": "notebook_environments", "value": 2},
                 {"company_id": ACTIVE, "cap": "disk", "value": 9}]  # a cap this version does not know

    async def run():
        store = await testing.async_redis(rebuilt=False, decode_responses=True)
        await publisher.publish_once(Pool(_rows(), overrides=overrides), _send_into(sent), "t", store)
        keys = sorted(await store.keys("quota:cap:*"))
        return {k: (await store.get(k), await store.ttl(k)) for k in keys}

    copies = asyncio.run(run())
    assert sent == [(ACTIVE, True, {"readings_per_second": 40, "notebook_environments": 2}),
                    (SUSPENDED, False, {})]
    assert {k: v for k, (v, _ttl) in copies.items()} == {f"quota:cap:notebook_environments:{ACTIVE}": "2",
                                                       f"quota:cap:readings_per_second:{ACTIVE}": "40"}
    assert all(0 < ttl <= tenant_status.STALE_AFTER_SECONDS for _v, ttl in copies.values())


@pytest.fixture(autouse=True)
def fresh_publisher(monkeypatch):
    monkeypatch.setattr(publisher, "_last_overrides", {})
    monkeypatch.setattr(publisher, "_copied", set())


def test_unreadable_overrides_or_store_never_hold_back_the_statuses():
    sent = []
    asyncio.run(publisher.publish_once(Pool(_rows(), fail_overrides=True), _send_into(sent), "t", DownStore()))
    assert [(c, a) for c, a, _caps in sent] == [(ACTIVE, True), (SUSPENDED, False)]
    sent.clear()
    overrides = [{"company_id": ACTIVE, "cap": "readings_per_second", "value": 40}]
    asyncio.run(publisher.publish_once(Pool(_rows(), overrides=overrides), _send_into(sent), "t", DownStore()))
    assert sent[0] == (ACTIVE, True, {"readings_per_second": 40})


def test_a_round_that_cannot_read_the_overrides_sends_the_last_ones_read():
    sent = []
    overrides = [{"company_id": ACTIVE, "cap": "readings_per_second", "value": 2000}]
    asyncio.run(publisher.publish_once(Pool(_rows(), overrides=overrides), _send_into(sent), "t"))
    sent.clear()
    asyncio.run(publisher.publish_once(Pool(_rows(), fail_overrides=True), _send_into(sent), "t"))
    assert sent[0] == (ACTIVE, True, {"readings_per_second": 2000})


def test_a_cleared_overrides_copy_is_deleted_on_the_next_round():
    overrides = [{"company_id": ACTIVE, "cap": "notebook_environments", "value": 5}]

    async def run():
        store = await testing.async_redis(rebuilt=False, decode_responses=True)
        send = _send_into([])
        await publisher.publish_once(Pool(_rows(), overrides=overrides), send, "t", store)
        before = sorted(await store.keys("quota:cap:*"))
        await publisher.publish_once(Pool(_rows(), overrides=[]), send, "t", store)
        return before, sorted(await store.keys("quota:cap:*"))

    before, after = asyncio.run(run())
    assert before == [f"quota:cap:notebook_environments:{ACTIVE}"] and after == []


def _usage(company_id, store, gib, age=60.0):
    return {"company_id": company_id, "store": store, "bytes": int(gib * (1 << 30)), "age": age}


def test_a_tenant_over_a_stored_cap_is_named_on_its_status_and_copied_for_the_tracking_gateway(monkeypatch):
    """ADR-0049 dec 11, 12: its fresh measurement against the cap that holds for it, its override first."""
    from industryflow_contracts import quotas

    monkeypatch.setitem(publisher.DEFAULTS, quotas.STORED_DATABASE_GIB, 10)
    monkeypatch.setitem(publisher.DEFAULTS, quotas.STORED_OBJECT_GIB, 10)
    usage = [_usage(ACTIVE, "database", 11), _usage(ACTIVE, "object", 11),
             _usage(SUSPENDED, "object", 11, age=publisher.USAGE_STALE_SECONDS + 1)]
    overrides = [{"company_id": ACTIVE, "cap": quotas.STORED_OBJECT_GIB, "value": 20}]
    sent = []

    async def run():
        store = await testing.async_redis()
        await publisher.publish_once(Pool(_rows(), overrides=overrides, usage=usage), _send_with_over(sent), "t", store)
        return store

    store = asyncio.run(run())
    over = {company_id: tenant_over for company_id, _active, _caps, tenant_over in sent}
    assert over[ACTIVE] == {quotas.STORED_DATABASE_GIB}      # the object override of 20 GiB holds it under
    assert over[SUSPENDED] == frozenset()                     # a stale measurement holds nothing (dec 8)

    async def copies():
        return (await store.get(quotas.over_key(quotas.STORED_DATABASE_GIB, ACTIVE)),
                await store.get(quotas.over_key(quotas.STORED_OBJECT_GIB, ACTIVE)))
    assert asyncio.run(copies())[0] is not None and asyncio.run(copies())[1] is None


def test_unreadable_measurements_hold_no_stored_cap_and_the_statuses_still_go_out():
    sent = []
    asyncio.run(publisher.publish_once(Pool(_rows(), fail_usage=True), _send_with_over(sent), "t"))
    assert len(sent) == 2 and all(tenant_over == frozenset() for *_rest, tenant_over in sent)
