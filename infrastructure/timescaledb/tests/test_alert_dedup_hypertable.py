# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert worker's durable dedup on the real alerts hypertable (ADR-0005 dec 6).

The dedup is one statement: a cooldown window check over the scope's prior alerts and an insert with
ON CONFLICT on (alert_id, triggered_at). On a hypertable each chunk holds its own unique index, so the
cases that differ from a plain table are the ones at a chunk boundary: a window whose earlier alert
sits in the previous chunk, an out-of-order event landing in the older chunk, and concurrent writers
creating the chunk an event falls into. Each case runs through the worker's own AlertRepository
against a tenant built by create_tenant_schema(), and the in-memory FakeAlertStore the engine's unit
tests use must reach the same outcome for every sequential case, so that fake cannot drift from the
SQL it stands in for.

Skips when no database is reachable; fails instead under IF_REQUIRE_LIVE_STACK=1.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

asyncpg = pytest.importorskip("asyncpg")

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
sys.path.insert(0, os.path.join(_ROOT, "services", "alert_service", "worker"))
sys.path.insert(0, os.path.join(_ROOT, "services", "alert_service", "tests"))

from fake_alert_store import FakeAlertStore  # noqa: E402
from repository import AlertRepository  # noqa: E402
from test_alert_dedup_live import _dsn  # noqa: E402

WINDOW = timedelta(seconds=300)
WRITERS = 8


def _unavailable(reason):
    if os.getenv("IF_REQUIRE_LIVE_STACK") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _alert(company_id, scope, at, *, alert_id=None, message="dedup"):
    rule_id, sensor_id, equipment_id, model_id = scope
    retrain = model_id is not None
    return {
        "company_id": str(company_id), "alert_id": alert_id or str(uuid.uuid4()), "triggered_at": at,
        "rule_id": None if retrain else rule_id, "sensor_id": sensor_id, "equipment_id": equipment_id,
        "model_id": model_id, "condition": "retrain_recommended" if retrain else None,
        "detection_type": "ml" if retrain else "threshold", "severity": "high", "message": message,
    }


def _scope(*, sensor=True, equipment=False, model=False):
    if model:
        return (None, None, None, str(uuid.uuid4()))
    return (str(uuid.uuid4()), str(uuid.uuid4()) if sensor else None,
            str(uuid.uuid4()) if equipment else None, None)


def _cases(company_id, boundary):
    """Sequential cases: (name, [(alert, window, expected_inserted, expected_prior_at)])."""
    b = boundary
    t = b - timedelta(days=2)  # well inside one chunk
    cases = []

    s = _scope()
    replay_id = str(uuid.uuid4())
    # Window zero: nothing but the primary key can stop the second insert.
    cases.append(("exact replay hits the hypertable's primary key", [
        (_alert(company_id, s, t, alert_id=replay_id), timedelta(0), True, None),
        (_alert(company_id, s, t, alert_id=replay_id), timedelta(0), False, None),
    ]))
    s = _scope()
    cases.append(("inside the window is suppressed", [
        (_alert(company_id, s, t), WINDOW, True, None),
        (_alert(company_id, s, t + timedelta(seconds=100)), WINDOW, False, t),
    ]))
    s = _scope()
    cases.append(("outside the window is a new alert", [
        (_alert(company_id, s, t), WINDOW, True, None),
        (_alert(company_id, s, t + timedelta(seconds=400)), WINDOW, True, None),
    ]))
    s = _scope()
    cases.append(("an earlier event inside the window is suppressed", [
        (_alert(company_id, s, t + timedelta(seconds=200)), WINDOW, True, None),
        (_alert(company_id, s, t), WINDOW, False, t + timedelta(seconds=200)),
    ]))
    s = _scope()
    cases.append(("the window reaches into the previous chunk", [
        (_alert(company_id, s, b - timedelta(seconds=60)), WINDOW, True, None),
        (_alert(company_id, s, b + timedelta(seconds=60)), WINDOW, False, b - timedelta(seconds=60)),
        (_alert(company_id, s, b + timedelta(seconds=400)), WINDOW, True, None),
    ]))
    s = _scope()
    cases.append(("an out-of-order event lands in the previous chunk and is suppressed", [
        (_alert(company_id, s, b + timedelta(seconds=60)), WINDOW, True, None),
        (_alert(company_id, s, b - timedelta(seconds=60)), WINDOW, False, b + timedelta(seconds=60)),
    ]))
    alert_scope, retrain_scope = _scope(), _scope(model=True)
    cases.append(("an alert and a retrain recommendation never suppress each other", [
        (_alert(company_id, alert_scope, t), WINDOW, True, None),
        (_alert(company_id, retrain_scope, t), WINDOW, True, None),
        (_alert(company_id, retrain_scope, t + timedelta(seconds=10)), WINDOW, False, t),
    ]))
    s = _scope(sensor=False, equipment=True)
    cases.append(("a scope with no sensor still deduplicates", [
        (_alert(company_id, s, t), WINDOW, True, None),
        (_alert(company_id, s, t + timedelta(seconds=30)), WINDOW, False, t),
        (_alert(company_id, _scope(sensor=False, equipment=True), t), WINDOW, True, None),
    ]))
    return cases


async def _run():
    try:
        # Every writer's connection open before a race starts, or connection setup serialises them.
        pool = await asyncio.wait_for(asyncpg.create_pool(**_dsn(), min_size=WRITERS, max_size=WRITERS), timeout=10)
    except Exception as e:  # noqa: BLE001 - any failure means "no DB here"
        _unavailable(f"no database reachable: {e}")
    company_id = uuid.uuid4()
    suffix = str(company_id).replace("-", "_")
    try:
        async with pool.acquire() as conn:
            if not await conn.fetchval("SELECT 1 FROM pg_proc WHERE proname = 'create_tenant_schema'"):
                _unavailable("create_tenant_schema() not present — schema sequence not applied")
            schema = await conn.fetchval("SELECT create_tenant_schema($1, $2)", str(company_id), f"dedup-{company_id}")
            assert await conn.fetchval(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = $1 AND hypertable_name = 'alerts'", schema) == 1

            # A chunk boundary a week ahead, taken from the hypertable itself.
            probe = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=7)
            await conn.execute(
                f'INSERT INTO "{schema}".alerts (triggered_at, detection_type, severity, message) '
                "VALUES ($1, 'probe', 'low', 'probe')", probe)
            boundary = await conn.fetchval(
                "SELECT range_end FROM timescaledb_information.chunks WHERE hypertable_schema = $1 "
                "AND hypertable_name = 'alerts' AND $2 >= range_start AND $2 < range_end", schema, probe)
            await conn.execute(f'DELETE FROM "{schema}".alerts')

        repo = AlertRepository(pool)
        outcomes = {}
        for name, steps in _cases(company_id, boundary):
            got = []
            for alert, window, *_ in steps:
                retrain = alert["model_id"] is not None
                got.append(await repo.save_alert_once(alert, window=window, retrain=retrain))
            outcomes[name] = (steps, got)

        async with pool.acquire() as conn:
            chunks = await conn.fetch(
                f'SELECT DISTINCT tableoid::regclass::text AS chunk FROM "{schema}".alerts '
                "WHERE triggered_at BETWEEN $1 AND $2",
                boundary - timedelta(seconds=60), boundary + timedelta(seconds=400))

        async def race(at):
            # Concurrent writers in one scope, event times a second apart on both sides of `at`.
            s = _scope()
            racers = [_alert(company_id, s, at + timedelta(seconds=i - WRITERS // 2), message=f"writer {i}")
                      for i in range(WRITERS)]
            results = await asyncio.gather(*(repo.save_alert_once(a, window=WINDOW) for a in racers))
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f'SELECT tableoid::regclass::text AS chunk FROM "{schema}".alerts WHERE rule_id = $1',
                    uuid.UUID(s[0]))
            return sum(1 for inserted, _ in results if inserted), [r["chunk"] for r in rows]

        # A first concurrent round on fresh connections is staggered by statement preparation and
        # never really overlaps; warm every connection in a throwaway scope before racing.
        await race(boundary - timedelta(days=3))
        # Across a boundary whose two chunks exist: only the scope's advisory lock serialises them.
        # (The boundary cases above created both chunks.)
        across = await race(boundary)
        # Across a boundary whose chunks do not exist yet: the writers create them while racing.
        creating = await race(boundary + timedelta(days=14))
        return outcomes, [r["chunk"] for r in chunks], across, creating
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "tenant_{suffix}" CASCADE')
            await conn.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
            await conn.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        await pool.close()


@pytest.fixture(scope="module")
def run():
    return asyncio.run(_run())


def test_every_case_behaves_on_the_hypertable_as_the_dedup_promises(run):
    outcomes, *_ = run
    wrong = []
    for name, (steps, got) in outcomes.items():
        want = [(inserted, prior) for _, _, inserted, prior in steps]
        if got != want:
            wrong.append(f"{name}: got {got}, want {want}")
    assert not wrong, "\n".join(wrong)


def test_the_boundary_cases_really_span_two_chunks(run):
    _, chunks, *_ = run
    assert len(chunks) == 2, f"the boundary cases did not straddle a chunk boundary: {chunks}"


def test_concurrent_writers_across_existing_chunks_leave_one_alert(run):
    *_, across, _ = run
    inserted, rows = across
    assert (inserted, len(rows)) == (1, 1), f"{inserted} inserted, rows in {rows}"


def test_concurrent_writers_creating_chunks_leave_one_alert(run):
    *_, creating = run
    inserted, rows = creating
    assert (inserted, len(rows)) == (1, 1), f"{inserted} inserted, rows in {rows}"


def test_the_fake_store_agrees_with_the_hypertable(run):
    # The engine's unit tests stand on FakeAlertStore; it must decide every sequential case as the
    # real statement did.
    outcomes, *_ = run
    disagree = []
    for name, (steps, got) in outcomes.items():
        fake = FakeAlertStore()
        mine = []
        for alert, window, *_ in steps:
            mine.append(asyncio.run(fake.save_alert_once(alert, window=window, retrain=alert["model_id"] is not None)))
        if mine != got:
            disagree.append(f"{name}: fake {mine}, hypertable {got}")
    assert not disagree, "\n".join(disagree)
