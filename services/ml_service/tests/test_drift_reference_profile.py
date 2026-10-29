# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The drift baseline's format (ADR-0035): a supplied feature sample, scores the platform derives.

Pure python/numpy — no DB, no MLflow, no evidently. The live drift score against it is exercised in
test_drift_score.py.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from drift import (  # noqa: E402
    REFERENCE_PROFILE_VERSION,
    accepted_sample,
    baseline_state,
    build_reference_profile,
    refused_baseline,
    sample_refusal,
    sample_rows,
    supplied_scores_refusal,
    with_derived_scores,
    without_prediction_baseline,
)
from drift.reference_profile import (  # noqa: E402
    DEFAULT_MAX_SAMPLE_ROWS,
    DEFAULT_PREDICTION_COLUMN,
    _sample_indices,
    prediction_column_refusal,
)
from extensions import SCORE_RANGE  # noqa: E402


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


def test_derived_scores_are_stored_as_the_prediction_column():
    prof = with_derived_scores(build_reference_profile({"a": [1.0, 2.0, 3.0]}), [0.1, 0.9], derived_at="t")
    assert prof["prediction_column"] == DEFAULT_PREDICTION_COLUMN
    assert prof["sample"][DEFAULT_PREDICTION_COLUMN] == [0.1, 0.9]
    assert prof["prediction_baseline"] == {"origin": "derived", "derived_at": "t"}
    assert prediction_column_refusal(prof) is None


def test_the_builder_offers_no_score_column():
    # ADR-0035 dec 2: the supplier provides the sample only.
    with pytest.raises(TypeError):
        build_reference_profile({"a": [1.0]}, prediction_scores=[0.5])


def test_nan_and_inf_become_null_for_json():
    prof = build_reference_profile({"s": [1.0, float("nan"), float("inf")]})
    assert prof["sample"]["s"] == [1.0, None, None]
    json.dumps(prof)  # must not raise (NaN/inf are not valid JSON)


def test_mismatched_column_lengths_raise():
    with pytest.raises(ValueError):
        build_reference_profile({"a": [1.0, 2.0], "b": [3.0]})


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


# --- the prediction column holds detector scores on the declared scale (ADR-0021 dec 1) ----

@pytest.mark.parametrize("scores", [
    [-1.0, 1.0, 1.0],          # an IsolationForest's labels
    [0.1, 1.7, 0.5],           # a raw output past the bounded scale
    [0.1, float("nan"), 0.5],  # not a score at all
])
def test_derived_scores_off_the_declared_scale_are_refused(scores):
    with pytest.raises(ValueError, match="not detector scores"):
        with_derived_scores(build_reference_profile({"a": [1.0, 2.0, 3.0]}), scores, derived_at="t")


def test_the_scale_bounds_are_the_detector_contracts():
    low, high = SCORE_RANGE
    prof = with_derived_scores(build_reference_profile({"a": [1.0, 2.0]}), [low, high], derived_at="t")
    assert prediction_column_refusal(prof) is None


@pytest.mark.parametrize("values, why", [
    ([0, 1, -1, 1], "outside the detector's declared scale"),
    ([0.2, None, 0.4], "not a score"),
    ([0.2, True, 0.4], "not a score"),
    ([], "holds no scores"),
])
def test_a_stored_prediction_column_off_the_scale_is_not_a_baseline(values, why):
    prof = build_reference_profile({"a": [1.0, 2.0]})
    prof["prediction_column"] = DEFAULT_PREDICTION_COLUMN
    prof["sample"][DEFAULT_PREDICTION_COLUMN] = values
    reason = prediction_column_refusal(prof)
    assert reason and why in reason, reason


def test_a_named_prediction_column_with_no_values_is_not_a_baseline():
    prof = build_reference_profile({"a": [1.0, 2.0]})
    prof["prediction_column"] = DEFAULT_PREDICTION_COLUMN
    assert "carries no values" in prediction_column_refusal(prof)


def test_a_profile_without_a_prediction_column_needs_none():
    assert prediction_column_refusal(build_reference_profile({"a": [1.0, 2.0]})) is None


# --- what registration accepts (ADR-0035 dec 2, 4) ------------------------------------------------

def _sample(**cols):
    return build_reference_profile(cols or {"temp": [1.0, 2.0, 3.0], "load": [4.0, None, 6.0]})


def test_a_sample_of_the_configurations_inputs_is_accepted():
    assert sample_refusal(_sample(), ["load", "temp"]) is None


@pytest.mark.parametrize("base, why", [
    (["temp"], "not the feature configuration's inputs"),
    (["temp", "load", "rpm"], "not the feature configuration's inputs"),
    ([], "no feature configuration"),
])
def test_a_sample_that_is_not_the_bound_inputs_is_refused(base, why):
    assert why in sample_refusal(_sample(), base)


@pytest.mark.parametrize("mutate, why", [
    (lambda p: p.update(schema_version=1), "schema_version"),
    (lambda p: p["sample"].update(temp=[1.0, "x", 3.0]), "not a number"),
    (lambda p: p["sample"].update(temp=[1.0, True, 3.0]), "not a number"),
    (lambda p: p["sample"].update(temp=[1.0, 2.0]), "differ in length"),
    (lambda p: p["sample"].update(temp=[]), "no values"),
    (lambda p: p.pop("sample"), "no columns and values"),
])
def test_a_malformed_sample_is_refused(mutate, why):
    prof = _sample()
    mutate(prof)
    assert why in sample_refusal(prof, ["temp", "load"])


def test_a_sample_carrying_scores_is_not_accepted():
    prof = with_derived_scores(_sample(), [0.1], derived_at="t")
    assert "derives them" in supplied_scores_refusal(prof)
    assert supplied_scores_refusal(_sample()) is None


def test_an_accepted_sample_keeps_only_its_own_fields():
    prof = _sample()
    prof["extra"] = "x" * 10
    kept = accepted_sample(prof)
    assert "extra" not in kept and kept["columns"] == ["temp", "load"] and kept["n_samples"] == 3


def test_an_accepted_sample_is_bounded_in_rows_on_every_path():
    # ADR-0035 dec 8: a direct registration or an upload is held to the cap the notebook client builds to.
    n = DEFAULT_MAX_SAMPLE_ROWS * 2 + 1
    prof = _sample()
    prof["sample"] = {"temp": [float(i) for i in range(n)], "load": [float(-i) for i in range(n)]}
    prof["n_training_rows"] = 3
    kept = accepted_sample(prof)
    assert kept["n_samples"] == DEFAULT_MAX_SAMPLE_ROWS == len(kept["sample"]["temp"])
    assert kept["n_training_rows"] == n
    assert kept["sample"]["temp"][0] == 0.0 and kept["sample"]["temp"][-1] == float(n - 1)
    assert all(t == -l for t, l in zip(kept["sample"]["temp"], kept["sample"]["load"]))


def test_rows_with_a_missing_value_are_not_scored():
    assert sample_rows(_sample()) == [{"temp": 1.0, "load": 4.0}, {"temp": 3.0, "load": 6.0}]


def test_the_baseline_says_what_monitoring_can_do():
    assert baseline_state(None)["data_drift"] is False
    assert baseline_state(refused_baseline("why"))["reason"] == "why"
    assert baseline_state(_sample())["prediction"] is None and baseline_state(_sample())["data_drift"]
    derived = with_derived_scores(_sample(), [0.2], derived_at="t")
    assert baseline_state(derived)["prediction"] == "derived"
    assert baseline_state(without_prediction_baseline(derived, "cannot"))["reason"] == "cannot"
    assert DEFAULT_PREDICTION_COLUMN not in without_prediction_baseline(derived, "x")["sample"]
    legacy = _sample()
    legacy["prediction_column"] = DEFAULT_PREDICTION_COLUMN
    legacy["sample"][DEFAULT_PREDICTION_COLUMN] = [0.1, 0.2, 0.3]
    assert baseline_state(legacy)["prediction"] == "supplied"


# --- the notebook client's copy of the format (ADR-0040 dec 4) -----------------------------------

def _client_drift():
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "clients", "python", "industryflow", "drift.py")
    spec = importlib.util.spec_from_file_location("client_drift", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("data, cap", [
    ({"temp": [1.0, float("nan"), 3.0], "load": [4.0, 5.0, float("inf")]}, 5000),
    ({"x": [float(v) for v in range(10_001)]}, 777),
])
def test_the_notebook_client_builds_the_same_sample(data, cap):
    # Two copies drift silently: a client sample the service reads differently is a baseline nobody
    # can trust, and a refusal at promotion the notebook author cannot explain.
    client = _client_drift()
    assert client.feature_sample(data, max_sample_rows=cap) == build_reference_profile(data, max_sample_rows=cap)
    assert client.FEATURE_SAMPLE_ARTIFACT == __import__("drift").FEATURE_SAMPLE_ARTIFACT
    assert client.REFERENCE_PROFILE_VERSION == REFERENCE_PROFILE_VERSION
    assert client.DEFAULT_MAX_SAMPLE_ROWS == DEFAULT_MAX_SAMPLE_ROWS
