# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert worker's durable dedup on the real alerts hypertable (ADR-0005 dec 6).

The dedup is one statement: a cooldown window check over the scope's prior alerts and an insert with
ON CONFLICT on (alert_id, triggered_at). On a hypertable each chunk holds its own unique index, so the
cases that differ from a plain table are the ones at a chunk boundary: a window whose earlier alert
sits in the previous chunk, an out-of-order event landing in the older chunk, and concurrent writers
creating the chunk an event falls into. Every sequential scenario is the shared table in
services/alert_service/tests/dedup_scenarios.py, run through the worker's own AlertRepository against a
tenant built by create_tenant_schema(); the alert_service unit suite runs the same table against the
in-memory FakeAlertStore the engine's tests use, so that fake cannot drift from the SQL it stands in
for.

Skips when no database is reachable; fails instead under IF_REQUIRE_LIVE_STACK=1.
"""
import asyncio
import importlib.util
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

asyncpg = pytest.importorskip("asyncpg")

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
sys.path.insert(0, os.path.join(_ROOT, "services", "alert_service", "worker"))


def _load(rel):
    spec = importlib.util.spec_from_file_location(os.path.basename(rel)[:-3], os.path.join(_ROOT, rel))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The scenario table and the fake the alert_service unit suite runs it against (dedup_scenarios.py).
dedup_scenarios = _load("services/alert_service/tests/dedup_scenarios.py")
FakeAlertStore = _load("services/alert_service/tests/fake_alert_store.py").FakeAlertStore

from repository import AlertRepository  # noqa: E402
from test_alert_dedup_live import _dsn  # noqa: E402

WINDOW = timedelta(seconds=300)
WRITERS = 8


def _unavailable(reason):
    if os.getenv("IF_REQUIRE_LIVE_STACK") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _alert(company_id, rule_id, at, *, message="dedup"):
    return {
        "company_id": str(company_id), "alert_id": str(uuid.uuid4()), "triggered_at": at,
        "rule_id": rule_id, "sensor_id": str(uuid.uuid4()), "equipment_id": None,
        "detection_type": "threshold", "severity": "high", "message": message,
        "degraded": False, "neutralized_features": [],
    }


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

        async def acknowledge(model_id):
            async with pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE "{schema}".alerts SET acknowledged = true '
                    "WHERE model_id = $1 AND condition = 'retrain_recommended'", uuid.UUID(model_id))

        bases = {"inside": boundary - timedelta(days=2), "chunk_boundary": boundary}
        outcomes, chunks = {}, set()
        for scenario in dedup_scenarios.SCENARIOS:
            base = bases[scenario.base]
            got, want = await dedup_scenarios.run(repo, scenario, base, str(company_id), acknowledge)
            outcomes[scenario.name] = (scenario, base, got, want)
            async with pool.acquire() as conn:
                if scenario.base == "chunk_boundary":
                    chunks |= {r["chunk"] for r in await conn.fetch(
                        f'SELECT DISTINCT tableoid::regclass::text AS chunk FROM "{schema}".alerts')}
                # Each scenario starts on an empty table, as the fake's does: a NULL scope part is the
                # same scope in every scenario.
                await conn.execute(f'DELETE FROM "{schema}".alerts')

        async def race(at):
            # Concurrent writers in one scope, event times a second apart on both sides of `at`.
            rule_id = str(uuid.uuid4())
            racers = [_alert(company_id, rule_id, at + timedelta(seconds=i - WRITERS // 2), message=f"writer {i}")
                      for i in range(WRITERS)]
            for racer in racers:  # one scope: one rule on one sensor
                racer["sensor_id"] = racers[0]["sensor_id"]
            results = await asyncio.gather(*(repo.save_alert_once(a, window=WINDOW) for a in racers))
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    f'SELECT tableoid::regclass::text AS chunk FROM "{schema}".alerts WHERE rule_id = $1',
                    uuid.UUID(rule_id))
            return sum(1 for inserted, _ in results if inserted), [r["chunk"] for r in rows]

        # A first concurrent round on fresh connections is staggered by statement preparation and
        # never really overlaps; warm every connection in a throwaway scope before racing.
        await race(boundary - timedelta(days=3))
        # Across a boundary whose two chunks exist: only the scope's advisory lock serialises them.
        # (The boundary cases above created both chunks.)
        across = await race(boundary)
        # Across a boundary whose chunks do not exist yet: the writers create them while racing.
        creating = await race(boundary + timedelta(days=14))
        return outcomes, sorted(chunks), across, creating
    finally:
        async with pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "tenant_{suffix}" CASCADE')
            await conn.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
            await conn.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        await pool.close()


@pytest.fixture(scope="module")
def run():
    return asyncio.run(_run())


def test_every_shared_scenario_ran(run):
    outcomes, *_ = run
    assert list(outcomes) == [sc.name for sc in dedup_scenarios.SCENARIOS]


def test_every_scenario_behaves_on_the_hypertable_as_the_dedup_promises(run):
    outcomes, *_ = run
    wrong = [f"{name}: got {got}, want {want}" for name, (_, _, got, want) in outcomes.items() if got != want]
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
    # The engine's unit tests stand on FakeAlertStore; it must decide every scenario as the real
    # statement did, from the same base.
    outcomes, *_ = run
    disagree = []
    for name, (scenario, base, got, _) in outcomes.items():
        mine, _ = asyncio.run(dedup_scenarios.run(FakeAlertStore(), scenario, base, "fake"))
        if mine != got:
            disagree.append(f"{name}: fake {mine}, hypertable {got}")
    assert not disagree, "\n".join(disagree)
