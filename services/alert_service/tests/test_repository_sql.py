# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The worker repository's statements, against a recording fake of the asyncpg pool.

- The tenant search_path is transaction-scoped (ADR-0003 dec 6): every tenant query runs inside a
  transaction that set it with SET LOCAL, never with a session SET that outlives the checkout.
- save_alert_once serialises its scope with a transaction-scoped advisory lock taken before the
  window check, which is what makes the cooldown hold across replicas.
- The dedup statement keeps its window predicate and its primary-key no-op.

The live proof against a real database is infrastructure/timescaledb/tests/test_alert_dedup_live.py.
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import repository  # noqa: E402
from repository import AlertRepository, dedup_lock_key  # noqa: E402

CO = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
CO_B = "9d6c1c1e-3f8e-4a52-8f53-1d1f0b7c2a10"
SCHEMA = "tenant_0b2f503a_6e59_4f4f_b0cd_b82547a20cf2"
RULE = "44444444-4444-4444-4444-444444444444"
SENSOR = "11111111-1111-1111-1111-111111111111"
MODEL = "33333333-3333-3333-3333-333333333333"


class Conn:
    def __init__(self, log):
        self.log = log
        self.in_tx = False

    @asynccontextmanager
    async def transaction(self):
        self.in_tx = True
        self.log.append(("BEGIN",))
        try:
            yield
        finally:
            self.log.append(("END",))
            self.in_tx = False

    def _record(self, sql, args):
        self.log.append((" ".join(sql.split()), args, self.in_tx))

    async def execute(self, sql, *args):
        self._record(sql, args)

    async def fetch(self, sql, *args):
        self._record(sql, args)
        return []

    async def fetchrow(self, sql, *args):
        self._record(sql, args)
        if "INSERT INTO alerts" in sql:
            return {"inserted": args[0], "prior_at": None}
        return {"tp": 0, "fp": 0}

    async def fetchval(self, sql, *args):
        self._record(sql, args)
        return None


class Pool:
    def __init__(self):
        self.log = []

    @asynccontextmanager
    async def acquire(self):
        yield Conn(self.log)


def _alert(**over):
    alert = {"company_id": CO, "alert_id": "a", "triggered_at": datetime(2026, 10, 13, tzinfo=timezone.utc),
             "rule_id": RULE, "sensor_id": SENSOR, "equipment_id": None, "detection_type": "threshold",
             "severity": "high", "message": "m"}
    alert.update(over)
    return alert


def _statements(log):
    return [entry for entry in log if len(entry) == 3]


def _every_repository_call(repo):
    return [
        repo.get_active_rules(CO),
        repo.save_alert_once(_alert(), window=timedelta(seconds=300)),
        repo.get_sensor_name(SENSOR, CO),
        repo.get_feature_config(SENSOR, CO),
        repo.get_model_by_id(MODEL, CO),
        repo.get_retrain_signals(MODEL, CO, 30, 72),
    ]


def test_search_path_is_only_ever_set_transaction_locally():
    for call in _every_repository_call(AlertRepository(pool := Pool())):
        pool.log.clear()
        asyncio.run(call)
        stmts = _statements(pool.log)
        assert stmts[0][0] == f'SET LOCAL search_path TO "{SCHEMA}", public'
        assert all(in_tx for _, _, in_tx in stmts), stmts
        assert not any(s.startswith("SET search_path") for s, _, _ in stmts)


def test_save_alert_once_locks_its_scope_before_the_window_check():
    pool = Pool()
    asyncio.run(AlertRepository(pool).save_alert_once(_alert(), window=timedelta(seconds=300)))
    stmts = [s for s, _, _ in _statements(pool.log)]
    lock = stmts.index("SELECT pg_advisory_xact_lock($1)")
    insert = next(i for i, s in enumerate(stmts) if "INSERT INTO alerts" in s)
    assert lock < insert
    assert _statements(pool.log)[lock][1] == (dedup_lock_key(SCHEMA, ("alert", RULE, SENSOR, None)),)


def test_dedup_lock_key_separates_scopes_and_tenants():
    schema_b = repository.normalize_company_id_to_schema(CO_B)
    a = dedup_lock_key(SCHEMA, ("alert", RULE, SENSOR, None))
    assert a == dedup_lock_key(SCHEMA, ("alert", RULE, SENSOR, None))
    assert a != dedup_lock_key(schema_b, ("alert", RULE, SENSOR, None))
    assert a != dedup_lock_key(SCHEMA, ("alert", RULE, None, SENSOR))
    assert a != dedup_lock_key(SCHEMA, ("retrain", MODEL))
    assert -(2 ** 63) <= a < 2 ** 63


def test_retrain_recommendations_lock_on_the_model():
    pool = Pool()
    alert = _alert(model_id=MODEL, condition="retrain_recommended")
    asyncio.run(AlertRepository(pool).save_alert_once(alert, window=timedelta(days=1), retrain=True))
    lock = next(e for e in _statements(pool.log) if e[0] == "SELECT pg_advisory_xact_lock($1)")
    assert lock[1] == (dedup_lock_key(SCHEMA, ("retrain", MODEL)),)


def test_dedup_statement_checks_the_window_both_ways_and_no_ops_an_exact_replay():
    sql = " ".join(repository._SAVE_ALERT_ONCE.split())
    assert "triggered_at > $2::timestamptz - $15::interval" in sql
    assert "triggered_at < $2::timestamptz + $15::interval" in sql
    assert "WHERE (SELECT prior_at FROM prior) IS NULL" in sql
    assert "ON CONFLICT (alert_id, triggered_at) DO NOTHING" in sql
    assert "condition IS DISTINCT FROM 'retrain_recommended'" in repository._ALERT_SCOPE
    assert "condition = 'retrain_recommended'" in repository._RETRAIN_SCOPE


def test_rules_load_only_for_schemas_that_are_exactly_a_tenants():
    """The worker enumerates tenants from the catalog (it serves them all). A schema that merely starts
    like one — role-shaped or a hand-made look-alike — is not a tenant, and its "company_id" must not be
    invented by string surgery on the name (ADR-0003 dec 5: one mapping, inverted strictly)."""
    names = (SCHEMA, "tenant_reader_0b2f503a_6e59_4f4f_b0cd_b82547a20cf2", "tenant_registry", SCHEMA.upper())

    class CatalogConn(Conn):
        async def fetch(self, sql, *args):
            self._record(sql, args)
            if "information_schema.schemata" in sql:
                return [{"schema_name": n} for n in names]
            return []

    class CatalogPool(Pool):
        @asynccontextmanager
        async def acquire(self):
            yield CatalogConn(self.log)

    pool = CatalogPool()
    rules = asyncio.run(repository.RuleRepository(pool).load_all_tenant_rules())
    assert rules == {CO: []}
    listing = next(e for e in _statements(pool.log) if "information_schema.schemata" in e[0])
    assert listing[1] == (repository.tenancy.SCHEMA_LIKE,), "the LIKE pattern is bound, escaped, from the mapping"
