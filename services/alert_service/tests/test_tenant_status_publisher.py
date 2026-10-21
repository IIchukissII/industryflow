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
from industryflow_contracts import tenancy, tenant_status  # noqa: E402

ACTIVE = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
SUSPENDED = "11111111-2222-3333-4444-555555555555"


class Pool:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.queries = rows, fail, []

    @asynccontextmanager
    async def acquire(self):
        pool = self

        class Conn:
            async def fetch(self, sql, *args):
                pool.queries.append((sql, args))
                if pool.fail:
                    raise ConnectionError("database unreachable")
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
    [(sql, args)] = pool.queries
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
