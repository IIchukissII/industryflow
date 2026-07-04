# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the drift reference-profile builder (ADR-0021 decision 4).

Pure python/numpy — no DB, no MLflow, no evidently. The profile is an evidently-consumable
capped sample; the live drift score against it is exercised in test_drift_score.py.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from drift import build_reference_profile, REFERENCE_PROFILE_VERSION  # noqa: E402
from drift.reference_profile import (  # noqa: E402
    DEFAULT_PREDICTION_COLUMN,
    _sample_indices,
)


def test_basic_shape_and_versioning():
    prof = build_reference_profile({"temp": [1.0, 2.0, 3.0], "load": [4.0, 5.0, 6.0]})
    assert prof["schema_version"] == REFERENCE_PROFILE_VERSION == 2
    assert prof["method"] == "evidently_sample"
    assert prof["columns"] == ["temp", "load"]
    assert prof["prediction_column"] is None
    assert prof["n_training_rows"] == 3
    assert prof["n_samples"] == 3
    assert prof["sample"]["temp"] == [1.0, 2.0, 3.0]


def test_downsampling_caps_rows_deterministically():
    values = list(range(10_000))
    prof = build_reference_profile({"x": [float(v) for v in values]}, max_sample_rows=500)
    assert prof["n_training_rows"] == 10_000
    assert prof["n_samples"] == 500
    assert len(prof["sample"]["x"]) == 500
    # Deterministic, evenly spaced, endpoints preserved.
    assert prof["sample"]["x"][0] == 0.0
    assert prof["sample"]["x"][-1] == 9999.0


def test_no_downsampling_below_cap():
    idx = _sample_indices(100, 5000)
    assert list(idx) == list(range(100))


def test_prediction_column_is_stored_when_given():
    prof = build_reference_profile(
        {"a": [1.0, 2.0, 3.0]},
        prediction_scores=[0.1, 0.9, 0.5],
    )
    assert prof["prediction_column"] == DEFAULT_PREDICTION_COLUMN
    assert prof["sample"][DEFAULT_PREDICTION_COLUMN] == [0.1, 0.9, 0.5]


def test_nan_and_inf_become_null_for_json():
    prof = build_reference_profile({"s": [1.0, float("nan"), float("inf")]})
    assert prof["sample"]["s"] == [1.0, None, None]
    json.dumps(prof)  # must not raise (NaN/inf are not valid JSON)


def test_mismatched_column_lengths_raise():
    with pytest.raises(ValueError):
        build_reference_profile({"a": [1.0, 2.0], "b": [3.0]})


def test_prediction_length_mismatch_raises():
    with pytest.raises(ValueError):
        build_reference_profile({"a": [1.0, 2.0, 3.0]}, prediction_scores=[0.1, 0.2])


def test_empty_input_raises():
    with pytest.raises(ValueError):
        build_reference_profile({})


def test_empty_columns_raise():
    with pytest.raises(ValueError):
        build_reference_profile({"a": []})


def test_bad_max_sample_raises():
    with pytest.raises(ValueError):
        build_reference_profile({"a": [1.0]}, max_sample_rows=0)


def test_profile_is_json_round_trippable():
    prof = build_reference_profile({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    assert json.loads(json.dumps(prof))["columns"] == ["a", "b"]
