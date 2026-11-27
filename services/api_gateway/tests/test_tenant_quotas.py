# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quotas (ADR-0049): only a superuser reads or overrides a cap, an override goes through the
database's function with the user as its actor, a refusal of the database is answered precisely, and
which tenants were refused is read from the shared store.

The function itself is proven against the real database in
infrastructure/timescaledb/tests/test_tenant_quotas.py."""
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError

import dependencies
from industryflow_contracts import quotas, testing
from routers import tenant_quotas as tq

NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)
TENANT = uuid.UUID("0b2f503a-6e59-4f4f-b0cd-b82547a20cf2")
ROOT = SimpleNamespace(is_superuser=True, id=uuid.uuid4())


def _refusal(error):
    """``error`` as SQLAlchemy's asyncpg adapter raises it."""
    dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
    translated = dbapi._asyncpg_error_translate[asyncpg.PostgresError](str(error))
    translated.pgcode = translated.sqlstate = error.sqlstate
    translated.__cause__ = error
    return DBAPIError.instance("SELECT ...", {}, translated, dbapi.Error)


class Db:
    """Answers with ``row``/``rows``, and a read of the storage measurements with ``usage``."""

    def __init__(self, row=None, rows=(), fail=None, usage=()):
        self.calls, self.row, self.rows, self.fail = [], row, list(rows), fail
        self.usage = list(usage)
        self.committed = self.rolled_back = False

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if self.fail:
            raise self.fail
        rows = self.usage if "tenant_storage_usage" in str(statement) else self.rows
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: self.row, all=lambda: rows))

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def test_every_route_takes_the_superuser_gate():
    for route in tq.router.routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert dependencies.require_superuser in deps, route.path


def test_an_override_goes_through_the_function_with_the_user_as_its_actor():
    db = Db(row={"changed": True, "value": 40, "updated_at": NOW})
    out = asyncio.run(tq.set_quota(TENANT, "readings_per_second", tq.SetQuota(value=40), user=ROOT, db=db))
    (sql, params), = db.calls
    assert "public.set_tenant_quota(" in sql
    assert params == {"company_id": TENANT, "cap": "readings_per_second", "value": 40, "actor_id": ROOT.id}
    assert db.committed and (out.changed, out.override, out.effective) == (True, 40, 40)


def test_clearing_an_override_returns_the_tenant_to_the_default(monkeypatch):
    monkeypatch.setitem(tq.DEFAULTS, "notebook_environments", 3)
    db = Db(row={"changed": True, "value": None, "updated_at": NOW})
    out = asyncio.run(tq.set_quota(TENANT, "notebook_environments", tq.SetQuota(value=None), user=ROOT, db=db))
    assert db.calls[0][1]["value"] is None
    assert (out.override, out.effective) == (None, 3)


@pytest.mark.parametrize("value", [0, -1, "5", 5.0, True, quotas.MAX_VALUE + 1])
def test_a_value_that_is_not_a_count_is_refused_before_the_database(value):
    with pytest.raises(ValidationError):
        tq.SetQuota(value=value)


def test_an_unknown_cap_is_a_404_before_the_database():
    db = Db()
    with pytest.raises(HTTPException) as e:
        asyncio.run(tq.set_quota(TENANT, "disk", tq.SetQuota(value=5), user=ROOT, db=db))
    assert e.value.status_code == 404 and db.calls == []


@pytest.mark.parametrize("sqlstate, status", [("P0002", 404), ("22023", 422)])
def test_a_refusal_of_the_database_is_answered_precisely(sqlstate, status):
    error = asyncpg.PostgresError("refused")
    error.sqlstate = sqlstate
    db = Db(fail=_refusal(error))
    with pytest.raises(HTTPException) as e:
        asyncio.run(tq.set_quota(TENANT, "readings_per_second", tq.SetQuota(value=5), user=ROOT, db=db))
    assert e.value.status_code == status and db.rolled_back and not db.committed


def test_a_tenants_caps_show_default_override_and_effective(monkeypatch):
    monkeypatch.setitem(tq.DEFAULTS, "readings_per_second", 250)
    monkeypatch.setitem(tq.DEFAULTS, "notebook_environments", None)
    db = Db(rows=[{"company_id": TENANT, "cap": "readings_per_second", "value": 40,
                   "updated_at": NOW, "updated_by": ROOT.id}])
    out = asyncio.run(tq.tenant_quotas(TENANT, user=ROOT, db=db))
    assert db.calls[0][1] == {"company_id": str(TENANT)}
    assert out.caps["readings_per_second"].model_dump() == {"default": 250, "override": 40, "effective": 40}
    assert out.caps["notebook_environments"].model_dump() == {"default": None, "override": None, "effective": None}
    assert out.stored == {}


def test_a_tenants_stored_data_is_shown_as_last_measured():
    db = Db(usage=[{"store": "object", "bytes": 123, "measured_at": NOW}])
    out = asyncio.run(tq.tenant_quotas(TENANT, user=ROOT, db=db))
    assert db.calls[1] == (tq.USAGE_SELECT, {"company_id": TENANT})
    assert out.stored["object"].model_dump() == {"bytes": 123, "measured_at": NOW}
    assert set(quotas.STORED_CAPS.values()) <= set(out.caps)


def test_the_tenants_without_a_fresh_measurement_are_counted_on_the_platform_plane():
    from messaging import quota_metrics

    db = Db(row={"unmeasured": 2})
    asyncio.run(quota_metrics.read_unmeasured(db))
    assert db.calls == [(quota_metrics.UNMEASURED_SELECT, {"stale": quota_metrics.USAGE_STALE_SECONDS})]
    assert quota_metrics.UNMEASURED._value.get() == 2


def test_which_tenants_were_refused_is_read_from_the_store_summed_over_the_days(monkeypatch):
    today = datetime.now(timezone.utc)
    other = "11111111-2222-3333-4444-555555555555"

    async def run():
        store = await testing.async_redis(rebuilt=False, decode_responses=True)
        monkeypatch.setattr(tq, "_store", lambda: store)
        day = today.strftime("%Y%m%d")
        await store.hset(quotas.refusals_key("readings_per_second", day), mapping={str(TENANT): 4, other: 1})
        await store.hset(quotas.refusals_key("readings_per_second", "19990101"), mapping={str(TENANT): 99})
        await store.hset(quotas.refused_last_key("readings_per_second", day), str(TENANT), int(today.timestamp()))
        await store.hset(quotas.refusals_key("notebook_environments", day), mapping={other: 2})
        return await tq.list_refusals(days=7, user=ROOT)

    out = asyncio.run(run())
    assert [(r.cap, str(r.company_id), r.refusals) for r in out] == [
        ("readings_per_second", str(TENANT), 4), ("notebook_environments", other, 2),
        ("readings_per_second", other, 1)]
    assert out[0].last_refused_at is not None and out[1].last_refused_at is None


def test_the_tenantless_refusal_counts_reach_the_platform_metrics_per_cap():
    from messaging import quota_metrics

    async def run():
        store = await testing.async_redis(rebuilt=False, decode_responses=True)
        await store.set(quotas.refusals_total_key("notebook_environments"), 7)
        await quota_metrics.read_once(store)

    asyncio.run(run())
    assert quota_metrics.REFUSALS.labels(cap="notebook_environments")._value.get() == 7
    assert quota_metrics.REFUSALS.labels(cap="readings_per_second")._value.get() == 0


def test_the_sql_proxys_counts_reach_the_platform_metrics():
    """The proxy exports no metrics (kernels reach it): its refusals at the total come from the quota
    store, and its sessions admitted without a count from the capability store (ADR-0049 dec 8, 10)."""
    from messaging import quota_metrics

    async def run():
        # One server stands for both stores here: their keys do not meet.
        store = await testing.async_redis(rebuilt=False, decode_responses=True)
        await store.set(quotas.SQL_SESSIONS_TOTAL_REFUSALS_KEY, 4)
        await store.set(quotas.refusals_total_key(quotas.SQL_STATEMENT_SECONDS), 2)
        await store.set(quotas.count_unavailable_key(quotas.SQL_SESSIONS), 3)
        await quota_metrics.read_once(store, store)

    asyncio.run(run())
    assert quota_metrics.TOTAL_REFUSALS._value.get() == 4
    assert quota_metrics.REFUSALS.labels(cap="sql_statement_seconds")._value.get() == 2
    assert quota_metrics.COUNT_UNAVAILABLE.labels(cap="sql_sessions")._value.get() == 3
