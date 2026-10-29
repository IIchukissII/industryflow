# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability-use record's one writer (ADR-0042 dec 4, 7), over a real stream and consumer group
(fakeredis) and a recording engine: an entry is acknowledged only once its batch committed, an
unreadable one is skipped and acknowledged, and a failed batch is delivered again.

The table and its writing function are proven against the real database in
infrastructure/timescaledb/tests/test_capability_audit.py.
"""
import uuid
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from industryflow_contracts import capability_audit as audit

from messaging import capability_audit_drain as drain

fakeredis = pytest.importorskip("fakeredis")


class Engine:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    @asynccontextmanager
    async def begin(self):
        pending = []

        class Conn:
            async def execute(_self, statement, params):
                assert "record_capability_use" in str(statement)
                pending.append(params)

        yield Conn()
        if self.fail:
            raise RuntimeError("database refused the batch")
        self.rows += pending


def _event(**kw):
    return audit.event(audit.MINTED, plane="hub", user="alice", company_id=str(uuid.uuid4()),
                       audience="sql", handle_id=str(uuid.uuid4()), **kw)


@pytest_asyncio.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await drain._ensure_group(r)
    await drain._ensure_group(r)  # idempotent: BUSYGROUP is not an error
    yield r


@pytest.mark.asyncio
async def test_entries_are_written_then_acknowledged(redis):
    ids = [await redis.xadd(**audit.append_args(_event())) for _ in range(3)]
    engine = Engine()
    assert await drain.drain_once(redis, engine, "c1", block_ms=10) == 3
    assert [r["stream_id"] for r in engine.rows] == ids
    assert (await redis.xpending(audit.STREAM_KEY, drain.GROUP))["pending"] == 0
    await drain._observe_backlog(redis)
    assert drain.BACKLOG._value.get() == 0


@pytest.mark.asyncio
async def test_an_unreadable_entry_is_skipped_and_does_not_stall_the_rest(redis):
    await redis.xadd(audit.STREAM_KEY, {"kind": "minted", "plane": "kernel", "at": "x"})
    good = await redis.xadd(**audit.append_args(_event()))
    engine = Engine()
    assert await drain.drain_once(redis, engine, "c1", block_ms=10) == 2
    assert [r["stream_id"] for r in engine.rows] == [good]


@pytest.mark.asyncio
async def test_a_refused_batch_is_not_acknowledged_and_comes_back(redis, monkeypatch):
    sid = await redis.xadd(**audit.append_args(_event()))
    with pytest.raises(RuntimeError):
        await drain.drain_once(redis, Engine(fail=True), "c1", block_ms=10)
    assert (await redis.xpending(audit.STREAM_KEY, drain.GROUP))["pending"] == 1
    # Another consumer claims it once it has been idle long enough.
    monkeypatch.setattr(drain, "CLAIM_IDLE_MS", 0)
    engine = Engine()
    assert await drain.drain_once(redis, engine, "c2", block_ms=10) == 1
    assert [r["stream_id"] for r in engine.rows] == [sid]
