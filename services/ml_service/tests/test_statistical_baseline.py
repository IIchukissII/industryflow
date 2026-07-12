# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for the aggregate-backed baseline path (ADR-0023 rev 1):

- the ``statistical`` transform degrades to the neutral value (0.0), never the raw sensor value,
  when the baseline is unavailable, and sources the mean from the baseline provider; and
- the ``AggregateBaselineProvider`` reads ``avg_value`` from the correct tenant aggregate table,
  caches within the TTL, validates the granularity/tenant, and degrades to ``None`` on error.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from extensions import get_transform, TransformContext  # noqa: E402
from feature_engineering.baseline_provider import AggregateBaselineProvider  # noqa: E402


EQUIPMENT_ID = "550e8400-e29b-41d4-a716-446655440100"
COMPANY_ID = "11111111-2222-3333-4444-555555555555"
STATISTICAL = get_transform("statistical")


# --------------------------------------------------------------------------- transform tests

class _FakeProvider:
    def __init__(self, mean=None, std=None, raises=False):
        self.mean = mean
        self.std = std
        self.raises = raises
        self.calls = []

    async def compute_baseline(self, company_id, equipment_id, sensor_name, granularity=None):
        self.calls.append({"company_id": company_id, "equipment_id": equipment_id,
                           "sensor_name": sensor_name, "granularity": granularity})
        if self.raises:
            raise RuntimeError("provider error")
        if self.mean is None:
            return None
        return {"mean": self.mean, "std": self.std}


def _transform(granularity=None, stat_type="deviation_from_window_mean"):
    t = {"name": "xmeas_1_deviation", "type": "statistical", "sensor": "xmeas_1",
         "params": {"stat_type": stat_type}}
    if granularity is not None:
        t["params"]["granularity"] = granularity
    return t


def _ctx(provider, equipment_id=EQUIPMENT_ID, company_id=COMPANY_ID):
    return TransformContext(baseline_provider=provider, equipment_id=equipment_id,
                            company_id=company_id)


@pytest.mark.asyncio
async def test_deviation_happy_path():
    result = await STATISTICAL(_transform(), {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0)))
    assert result == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_neutral_when_provider_raises():
    """Provider error yields neutral 0.0 — NOT the raw value (regression for the OOD-fallback bug)."""
    result = await STATISTICAL(_transform(), {"xmeas_1": 72.5}, _ctx(_FakeProvider(raises=True)))
    assert result == 0.0


@pytest.mark.asyncio
async def test_neutral_when_no_baseline():
    result = await STATISTICAL(_transform(), {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=None)))
    assert result == 0.0


@pytest.mark.asyncio
async def test_neutral_when_no_provider():
    result = await STATISTICAL(_transform(), {"xmeas_1": 72.5}, _ctx(None))
    assert result == 0.0


@pytest.mark.asyncio
async def test_neutral_when_no_company_id():
    result = await STATISTICAL(_transform(), {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0), company_id=None))
    assert result == 0.0


@pytest.mark.asyncio
async def test_granularity_passed_through():
    provider = _FakeProvider(mean=0.0)
    await STATISTICAL(_transform(granularity="5min"), {"xmeas_1": 1.0}, _ctx(provider))
    assert provider.calls[0]["granularity"] == "5min"
    assert provider.calls[0]["company_id"] == COMPANY_ID


@pytest.mark.asyncio
async def test_unknown_stat_type_is_neutral():
    t = {"name": "x", "type": "statistical", "sensor": "xmeas_1",
         "params": {"stat_type": "nope"}}
    assert await STATISTICAL(t, {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0))) == 0.0


@pytest.mark.asyncio
async def test_legacy_stat_type_still_resolves():
    """Configs written before ADR-0023 rev 2 keep working — the rename is not a silent break."""
    result = await STATISTICAL(_transform(stat_type="deviation_from_run_mean"),
                               {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0)))
    assert result == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_missing_stat_type_is_neutral():
    """No default: a config that names no statistic must not silently receive a deviation.

    Regression for the notebook-emitted `{"operation": "mean"}` params, which carried no stat_type
    and so fell through to the deviation default — serving a deviation into a mean's feature slot.
    """
    t = {"name": "xmeas_1_run_mean", "type": "statistical", "sensor": "xmeas_1",
         "params": {"operation": "mean", "groupby": "simulationRun"}}
    assert await STATISTICAL(t, {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0))) == 0.0


@pytest.mark.asyncio
async def test_window_mean_returns_the_mean():
    result = await STATISTICAL(_transform(stat_type="window_mean"),
                               {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0, std=1.5)))
    assert result == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_window_std_returns_the_std():
    result = await STATISTICAL(_transform(stat_type="window_std"),
                               {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0, std=1.5)))
    assert result == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_window_std_neutral_when_single_sample_window():
    """A one-sample window has no stddev; 'no spread' is 0.0, not the raw value."""
    result = await STATISTICAL(_transform(stat_type="window_std"),
                               {"xmeas_1": 72.5}, _ctx(_FakeProvider(mean=70.0, std=None)))
    assert result == 0.0


@pytest.mark.asyncio
async def test_missing_sensor_is_neutral():
    t = {"name": "bad", "type": "statistical", "params": {}}
    assert await STATISTICAL(t, {}, _ctx(_FakeProvider(mean=70.0))) == 0.0


# --------------------------------------------------------------------------- provider tests

class _FakeConn:
    def __init__(self, row=None, raise_on_fetch=False):
        self.row = row
        self.raise_on_fetch = raise_on_fetch
        self.executed = []
        self.fetched = []

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetchrow(self, sql, *args, timeout=None):
        self.fetched.append(sql)
        if self.raise_on_fetch:
            raise RuntimeError("db unreachable")
        return self.row


class _FakeAcquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        self.pool.acquire_count += 1
        return self.pool.conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.acquire_count = 0

    def acquire(self):
        return _FakeAcquire(self)


@pytest.mark.asyncio
async def test_provider_returns_avg_value():
    pool = _FakePool(_FakeConn(row={"avg_value": 70.0, "stddev_value": 1.5}))
    provider = AggregateBaselineProvider(pool)
    mean = await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    assert mean == 70.0


@pytest.mark.asyncio
async def test_provider_returns_mean_and_std():
    pool = _FakePool(_FakeConn(row={"avg_value": 70.0, "stddev_value": 1.5}))
    provider = AggregateBaselineProvider(pool)
    baseline = await provider.compute_baseline(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    assert baseline == {"mean": 70.0, "std": 1.5}


@pytest.mark.asyncio
async def test_provider_std_may_be_none():
    """A single-sample window has avg but no stddev — a present baseline with a null std."""
    pool = _FakePool(_FakeConn(row={"avg_value": 70.0, "stddev_value": None}))
    provider = AggregateBaselineProvider(pool)
    baseline = await provider.compute_baseline(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    assert baseline == {"mean": 70.0, "std": None}


@pytest.mark.asyncio
async def test_provider_selects_table_by_granularity():
    pool = _FakePool(_FakeConn(row={"avg_value": 1.0, "stddev_value": 0.5}))
    provider = AggregateBaselineProvider(pool)
    await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1", granularity="5min")
    assert any("sensor_aggregations_5min" in sql for sql in pool.conn.fetched)


@pytest.mark.asyncio
async def test_provider_default_granularity_is_1min():
    pool = _FakePool(_FakeConn(row={"avg_value": 1.0, "stddev_value": 0.5}))
    provider = AggregateBaselineProvider(pool)
    await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    assert any("sensor_aggregations_1min" in sql for sql in pool.conn.fetched)


@pytest.mark.asyncio
async def test_provider_caches_within_ttl():
    pool = _FakePool(_FakeConn(row={"avg_value": 70.0, "stddev_value": 1.5}))
    provider = AggregateBaselineProvider(pool, cache_ttl_seconds=60.0)
    await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    assert pool.acquire_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_provider_unknown_granularity_no_db():
    pool = _FakePool(_FakeConn(row={"avg_value": 70.0, "stddev_value": 1.5}))
    provider = AggregateBaselineProvider(pool)
    mean = await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1", granularity="7min")
    assert mean is None
    assert pool.acquire_count == 0


@pytest.mark.asyncio
async def test_provider_invalid_company_id_no_db():
    pool = _FakePool(_FakeConn(row={"avg_value": 70.0, "stddev_value": 1.5}))
    provider = AggregateBaselineProvider(pool)
    mean = await provider.compute_rolling_mean("not-a-uuid", EQUIPMENT_ID, "xmeas_1")
    assert mean is None
    assert pool.acquire_count == 0


@pytest.mark.asyncio
async def test_provider_db_error_is_none():
    pool = _FakePool(_FakeConn(raise_on_fetch=True))
    provider = AggregateBaselineProvider(pool)
    mean = await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    assert mean is None


@pytest.mark.asyncio
async def test_provider_no_row_is_none():
    pool = _FakePool(_FakeConn(row=None))
    provider = AggregateBaselineProvider(pool)
    mean = await provider.compute_rolling_mean(COMPANY_ID, EQUIPMENT_ID, "xmeas_1")
    assert mean is None
