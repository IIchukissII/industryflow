# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A transform type nothing registered implements is refused (ADR-0010): when a configuration is
written, and when an engine is built from one already stored, so no model is scored on a substitute
value it never saw. Found end to end: a configuration of type ``raw`` was accepted, a model bound to
it deployed, and every score came from zeros."""
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import extensions.builtins  # noqa: E402,F401 - registers the built-in transforms
from feature_engineering import FeatureEngineeringEngine, UnknownTransform  # noqa: E402
from routers import feature_configs  # noqa: E402

KNOWN = [{"name": "temp_1_raw", "type": "identity", "sensor": "temp_1"}]
UNKNOWN = [{"name": "temp_1_raw", "type": "raw", "sensor": "temp_1"}]


def _create(transformations):
    return feature_configs.FeatureConfigCreate(equipment_type="pump", name="c", base_sensors=["temp_1"],
                                               transformations=transformations)


def test_a_configuration_naming_an_unknown_type_is_refused_when_written():
    assert _create(KNOWN).transformations == KNOWN
    with pytest.raises(ValidationError, match="'raw'"):
        _create(UNKNOWN)
    with pytest.raises(ValidationError, match="'raw'"):
        feature_configs.FeatureConfigUpdate(transformations=UNKNOWN)
    with pytest.raises(ValidationError, match="name"):
        _create([{"type": "identity", "sensor": "temp_1"}])
    assert feature_configs.FeatureConfigUpdate(name="renamed").transformations is None


def test_an_engine_is_not_built_from_a_stored_configuration_naming_one():
    config = {"base_sensors": ["temp_1"], "transformations": UNKNOWN + KNOWN}
    with pytest.raises(UnknownTransform) as refused:
        FeatureEngineeringEngine(feature_config=config)
    assert refused.value.types == ["raw"]
    FeatureEngineeringEngine(feature_config={"base_sensors": ["temp_1"], "transformations": KNOWN})
