# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The stateful-feature kill-switch (ADR-0024 rev 1).

Two properties carry the whole decision, and both are easy to implement *almost* correctly:

1. **A killed class issues ZERO substrate calls** (dec 4). Nulling the result after calling the
   transform would protect the model and do nothing for the degraded database — the switch would
   look like it worked while the incident continued. So the test asserts on the provider's call
   count, not on the returned vector.
2. **The switch fails OPEN, on last-known-good** (dec 7). Failing closed would be auto-trip in
   disguise: a transient blip would neutralize a feature class with nobody deciding to, and it
   would flap. dec 6 defers auto-trip precisely because it needs hysteresis.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from extensions import (  # noqa: E402
    is_stateful, neutral_value, registered_transforms, stateful_transforms,
    EXTENSION_API_VERSION,
)
from feature_engineering.engine import FeatureEngineeringEngine  # noqa: E402
from feature_engineering.kill_switch import StatefulFeatureSwitch  # noqa: E402

COMPANY_ID = "11111111-2222-3333-4444-555555555555"
EQUIPMENT_ID = "550e8400-e29b-41d4-a716-446655440100"

FEATURE_CONFIG = {
    "base_sensors": ["xmeas_1"],
    "transformations": [
        {"name": "xmeas_1_identity", "type": "identity", "sensor": "xmeas_1"},
        {"name": "xmeas_1_squared", "type": "polynomial", "sensor": "xmeas_1",
         "params": {"power": 2}},
        {"name": "xmeas_1_deviation", "type": "statistical", "sensor": "xmeas_1",
         "params": {"stat_type": "deviation_from_window_mean", "granularity": "1min"}},
    ],
}
SENSOR_DATA = {"xmeas_1": 72.5}


class _CountingProvider:
    """A baseline provider that records every call — the substrate the switch must stop touching."""

    def __init__(self, mean=70.0):
        self.mean = mean
        self.calls = 0

    async def compute_baseline(self, company_id, equipment_id, sensor_name, granularity=None):
        self.calls += 1
        return {"mean": self.mean, "std": 1.0}


class _Switch:
    def __init__(self, enabled):
        self._enabled = enabled
        self.reads = 0

    async def enabled(self):
        self.reads += 1
        return self._enabled


def _engine(provider, switch=None):
    return FeatureEngineeringEngine(
        feature_config=FEATURE_CONFIG, baseline_provider=provider,
        equipment_id=EQUIPMENT_ID, company_id=COMPANY_ID, kill_switch=switch,
    )


# ------------------------------------------------------------------- the registry capability tag

def test_stateful_transforms_are_tagged():
    assert is_stateful("statistical")
    assert is_stateful("rolling_stat")
    assert stateful_transforms() == ["rolling_stat", "statistical"]


def test_stateless_transforms_are_not_tagged():
    for name in ("identity", "polynomial", "interaction", "deviation"):
        assert not is_stateful(name), f"{name} touches no external state"
    # Every registered transform must be classified one way or the other, so a new transform cannot
    # slip in unclassified and quietly escape the switch.
    for name in registered_transforms():
        assert isinstance(is_stateful(name), bool)


def test_unknown_type_is_not_stateful():
    """A typo in a config must not silently neutralize a feature."""
    assert not is_stateful("no_such_transform")
    assert neutral_value("no_such_transform") == 0.0


def test_extension_api_version_bumped_minor():
    """Additive capability tags — a minor bump, never a breaking major (ADR-0010 dec 3).

    The literal is the point: the contract's version may only move when someone means it to. It has
    moved twice, both additively — 0.2.0 for ADR-0024's `stateful`/`neutral` transform tags, and 0.3.0
    for ADR-0028's `semantics`/`handles_flavors` detector declarations. Both keep older extensions
    registering untouched, which is what makes them minors and not majors.
    """
    major, minor, _ = EXTENSION_API_VERSION.split(".")
    assert (int(major), int(minor)) == (0, 3)


# --------------------------------------------------------------- null-in-slot, and ZERO calls

@pytest.mark.asyncio
async def test_switch_on_computes_features_normally():
    provider = _CountingProvider()
    engine = _engine(provider, _Switch(enabled=True))
    features = await engine.transform(SENSOR_DATA)

    assert features.tolist() == [[72.5, 72.5 ** 2, pytest.approx(2.5)]]
    assert provider.calls == 1
    assert engine.neutralized_features == []


@pytest.mark.asyncio
async def test_switch_off_neutralizes_the_slot_and_makes_zero_substrate_calls():
    """The heart of dec 4: relief, not just masking. The provider must not be touched at all."""
    provider = _CountingProvider()
    engine = _engine(provider, _Switch(enabled=False))
    features = await engine.transform(SENSOR_DATA)

    assert provider.calls == 0, "a killed class must issue ZERO substrate calls, not neutralized ones"
    assert engine.neutralized_features == ["xmeas_1_deviation"]
    # Stateless features are untouched; only the stateful slot goes neutral.
    assert features.tolist() == [[72.5, 72.5 ** 2, 0.0]]


@pytest.mark.asyncio
async def test_switch_off_preserves_vector_shape_and_order():
    """The reason for null-in-slot rather than dropping the feature: a bound model expects a
    fixed-length, fixed-order vector. Killing features must not break every model."""
    on = await _engine(_CountingProvider(), _Switch(enabled=True)).transform(SENSOR_DATA)
    off = await _engine(_CountingProvider(), _Switch(enabled=False)).transform(SENSOR_DATA)

    assert on.shape == off.shape == (1, 3)
    engine = _engine(_CountingProvider(), _Switch(enabled=False))
    await engine.transform(SENSOR_DATA)
    assert engine.get_feature_names() == ["xmeas_1_identity", "xmeas_1_squared", "xmeas_1_deviation"]


@pytest.mark.asyncio
async def test_switch_read_once_per_inference_not_once_per_feature():
    """The switch's own read must not become the load it exists to relieve."""
    switch = _Switch(enabled=True)
    await _engine(_CountingProvider(), switch).transform(SENSOR_DATA)
    assert switch.reads == 1


@pytest.mark.asyncio
async def test_no_switch_behaves_as_before():
    """The switch is a control, not a requirement — an engine without one still computes."""
    provider = _CountingProvider()
    engine = _engine(provider, switch=None)
    features = await engine.transform(SENSOR_DATA)
    assert provider.calls == 1
    assert features.tolist() == [[72.5, 72.5 ** 2, pytest.approx(2.5)]]


# --------------------------------------------------------------------- the switch reads the DB

class _FakeConn:
    def __init__(self, value=None, raises=False):
        self.value = value
        self.raises = raises
        self.queries = []

    async def fetchval(self, sql, *args, timeout=None):
        self.queries.append(sql)
        if self.raises:
            raise RuntimeError("db unreachable")
        return self.value


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
@pytest.mark.parametrize("stored,expected", [
    (True, True), (False, False),          # asyncpg with a JSONB codec registered
    ("true", True), ("false", False),      # raw JSON text, no codec
])
async def test_switch_reads_the_flag(stored, expected):
    switch = StatefulFeatureSwitch(_FakePool(_FakeConn(value=stored)))
    assert await switch.enabled() is expected


@pytest.mark.asyncio
async def test_switch_reads_the_shared_schema_not_the_tenant_one():
    """The inference path may hold a tenant search_path; an unqualified name would resolve into the
    tenant schema and fail (ADR-0003)."""
    pool = _FakePool(_FakeConn(value=True))
    await StatefulFeatureSwitch(pool).enabled()
    assert all("public.platform_config" in sql for sql in pool.conn.queries)


@pytest.mark.asyncio
async def test_switch_caches_within_ttl():
    pool = _FakePool(_FakeConn(value=True))
    switch = StatefulFeatureSwitch(pool, cache_ttl_seconds=60.0)
    await switch.enabled()
    await switch.enabled()
    assert pool.acquire_count == 1


# ------------------------------------------------------------------------------- it fails OPEN

@pytest.mark.asyncio
async def test_never_read_fails_open():
    """Cold start against a dead DB: assume enabled. The baseline reads then fail to their own
    neutral, so inference still serves — and no feature class was killed by accident."""
    switch = StatefulFeatureSwitch(_FakePool(_FakeConn(raises=True)))
    assert await switch.enabled() is True


@pytest.mark.asyncio
async def test_failed_read_holds_last_known_value():
    """A blip must not flip the switch. Both directions: a DB that goes away holds whatever the
    operator last set — off stays off, on stays on."""
    for last_known in (True, False):
        conn = _FakeConn(value=last_known)
        switch = StatefulFeatureSwitch(_FakePool(conn), cache_ttl_seconds=0.0)
        assert await switch.enabled() is last_known

        conn.raises = True  # the DB goes away
        assert await switch.enabled() is last_known, "a failed read must hold, not flip"


@pytest.mark.asyncio
async def test_missing_row_is_enabled():
    """Migration not applied, or the row deleted: enabled is the documented default — never a
    surprise kill."""
    switch = StatefulFeatureSwitch(_FakePool(_FakeConn(value=None)))
    assert await switch.enabled() is True


@pytest.mark.asyncio
async def test_garbage_value_is_enabled():
    switch = StatefulFeatureSwitch(_FakePool(_FakeConn(value={"not": "a bool"})))
    assert await switch.enabled() is True


@pytest.mark.asyncio
async def test_failed_read_backs_off_rather_than_hammering():
    """The switch must not re-query a degraded DB on every inference — it would become the load it
    exists to relieve."""
    pool = _FakePool(_FakeConn(raises=True))
    switch = StatefulFeatureSwitch(pool, cache_ttl_seconds=60.0)
    await switch.enabled()
    await switch.enabled()
    await switch.enabled()
    assert pool.acquire_count == 1
