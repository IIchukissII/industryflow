# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The engine computes the bound vector from a complete reading, or refuses (ADR-0044).

A substitute in a slot yields a score indistinguishable from an ordinary one. The only substitute
left is ADR-0024's: a stateful slot neutralized by the switch, or one whose aggregate is
unavailable, and both are named.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import extensions  # noqa: E402
from extensions import (  # noqa: E402
    InvalidFeature, MissingSensors, StatefulUnavailable, register_transform, sensor_value,
)
from feature_engineering import FeatureEngineeringEngine  # noqa: E402

EQ, CID = "eq-1", "cid-1"


def _engine(transformations, base_sensors=("a", "b"), provider=None):
    return FeatureEngineeringEngine(
        feature_config={"base_sensors": list(base_sensors), "transformations": transformations},
        baseline_provider=provider, equipment_id=EQ, company_id=CID,
    )


IDENTITY = [{"name": "a", "type": "identity", "sensor": "a"}, {"name": "b", "type": "identity", "sensor": "b"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("reading,missing", [
    ({"a": 1.0}, ["b"]),
    ({}, ["a", "b"]),
    ({"a": None, "b": 2.0}, ["a"]),
    ({"a": math.nan, "b": math.inf}, ["a", "b"]),
    ({"a": "1.0", "b": True}, ["a", "b"]),
])
async def test_a_reading_lacking_a_declared_sensor_is_refused_naming_them(reading, missing):
    with pytest.raises(MissingSensors) as exc:
        await _engine(IDENTITY).transform(reading)
    assert exc.value.sensors == missing


@pytest.mark.asyncio
async def test_a_sensor_a_feature_names_counts_even_when_not_declared():
    engine = _engine([{"name": "c", "type": "identity", "sensor": "c"}], base_sensors=("a",))
    with pytest.raises(MissingSensors) as exc:
        await engine.transform({"a": 1.0})
    assert exc.value.sensors == ["c"]


@pytest.mark.asyncio
async def test_a_complete_reading_is_computed():
    assert (await _engine(IDENTITY).transform({"a": 1, "b": 2.5})).tolist() == [[1.0, 2.5]]


@pytest.mark.asyncio
@pytest.mark.parametrize("transformation,needle", [
    ({"name": "x", "type": "identity"}, "no sensor"),
    ({"name": "x", "type": "interaction", "sensors": ["a"]}, "needs 2 sensors"),
    ({"name": "x", "type": "interaction", "sensors": ["a", "b"], "params": {"operation": "pow"}}, "pow"),
    ({"name": "x", "type": "statistical", "sensor": "a", "params": {}}, "stat_type"),
    ({"name": "x", "type": "rolling_stat", "sensor": "a"}, "not implemented"),
])
async def test_a_definition_its_transform_cannot_interpret_is_refused(transformation, needle):
    with pytest.raises(InvalidFeature, match=needle):
        await _engine([transformation]).transform({"a": 1.0, "b": 2.0})


_TEST_TYPES = ("_test_raises", "_test_stateless_unavailable", "_test_stateful_unavailable", "_test_reads_undeclared")


@pytest.fixture(autouse=True, scope="module")
def _unregister_test_transforms():
    """The registry is process-global; the stand-ins below must not outlive this module."""
    yield
    for name in _TEST_TYPES:
        extensions._TRANSFORMS.pop(name, None)
        extensions._TRANSFORM_TAGS.pop(name, None)


@register_transform("_test_raises")
async def _raises(transformation, sensor_data, ctx):
    raise ZeroDivisionError("boom")


@register_transform("_test_stateless_unavailable")
async def _stateless_unavailable(transformation, sensor_data, ctx):
    raise StatefulUnavailable("only a stateful transform may report this")


@register_transform("_test_stateful_unavailable", stateful=True, neutral=-1.0)
async def _stateful_unavailable(transformation, sensor_data, ctx):
    raise StatefulUnavailable("store down")


@register_transform("_test_reads_undeclared")
async def _reads_undeclared(transformation, sensor_data, ctx):
    return sensor_value(sensor_data, "z")


@pytest.mark.asyncio
async def test_any_other_failure_fails_the_score_instead_of_a_zero():
    with pytest.raises(ZeroDivisionError):
        await _engine([{"name": "x", "type": "_test_raises"}]).transform({"a": 1.0, "b": 2.0})


@pytest.mark.asyncio
async def test_an_unavailable_stateful_slot_takes_its_neutral_and_is_named():
    engine = _engine([*IDENTITY, {"name": "s", "type": "_test_stateful_unavailable"}])
    assert (await engine.transform({"a": 1.0, "b": 2.0})).tolist() == [[1.0, 2.0, -1.0]]
    assert engine.unavailable_features == ["s"]
    assert engine.neutralized_features == [], "an unavailable aggregate is not a kill-switch flip"


@pytest.mark.asyncio
async def test_only_a_stateful_transform_may_report_unavailable():
    with pytest.raises(StatefulUnavailable):
        await _engine([{"name": "x", "type": "_test_stateless_unavailable"}]).transform({"a": 1.0, "b": 2.0})


@pytest.mark.asyncio
async def test_a_transform_reading_an_undeclared_sensor_is_refused_not_zeroed():
    with pytest.raises(MissingSensors) as exc:
        await _engine([{"name": "x", "type": "_test_reads_undeclared"}]).transform({"a": 1.0, "b": 2.0})
    assert exc.value.sensors == ["z"]
