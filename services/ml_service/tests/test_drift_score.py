# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the evidently drift scorer (ADR-0021 decision 1).

These run real evidently over in-memory reference/current frames — no DB, no MLflow, no
model load. The endpoint's live windowed read and the warm-model prediction generation are
cluster-bound and covered separately.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from drift import build_reference_profile, compute_drift, MIN_ROWS  # noqa: E402
from drift.score import _is_drifted  # noqa: E402

N = 500  # comfortably above MIN_ROWS for stable statistics


def _profile(cols, prediction=None):
    return build_reference_profile(cols, prediction_scores=prediction)


def test_identical_distribution_reports_low_share():
    rng = np.random.default_rng(1)
    ref = {"a": rng.normal(size=N).tolist(), "b": rng.normal(5, 2, size=N).tolist()}
    prof = _profile(ref)
    cur = {"a": rng.normal(size=N).tolist(), "b": rng.normal(5, 2, size=N).tolist()}
    res = compute_drift(prof, cur)
    assert res["status"] == "ok"
    assert res["data_drift"]["drift_share"] == 0.0
    assert res["data_drift"]["drift_detected"] is False
    assert res["prediction_drift"] is None


def test_shifted_feature_is_flagged():
    rng = np.random.default_rng(2)
    prof = _profile({"a": rng.normal(size=N).tolist(), "b": rng.normal(size=N).tolist()})
    cur = {"a": rng.normal(loc=6.0, size=N).tolist(),  # strong shift
           "b": rng.normal(size=N).tolist()}           # stable
    res = compute_drift(prof, cur)
    assert res["data_drift"]["n_columns"] == 2
    assert res["data_drift"]["n_drifted"] >= 1
    assert res["data_drift"]["drift_share"] >= 0.5
    assert res["data_drift"]["per_column"]["a"]["drifted"] is True


def test_prediction_drift_is_scored_when_reference_has_prediction_column():
    rng = np.random.default_rng(3)
    prof = _profile(
        {"a": rng.normal(size=N).tolist()},
        prediction=rng.uniform(0, 0.2, size=N).tolist(),  # low training anomaly scores
    )
    # Current window: same inputs, but model now scores much higher → prediction drift.
    cur = {
        "a": rng.normal(size=N).tolist(),
        "prediction_score": rng.uniform(0.7, 1.0, size=N).tolist(),
    }
    res = compute_drift(prof, cur)
    assert res["prediction_drift"] is not None
    assert res["prediction_drift"]["column"] == "prediction_score"
    assert res["prediction_drift"]["drifted"] is True


def test_prediction_column_absent_from_current_yields_none():
    rng = np.random.default_rng(4)
    prof = _profile({"a": rng.normal(size=N).tolist()},
                    prediction=rng.uniform(size=N).tolist())
    res = compute_drift(prof, {"a": rng.normal(size=N).tolist()})  # no prediction supplied
    assert res["prediction_drift"] is None


def test_no_overlapping_features_is_unavailable():
    prof = _profile({"a": [float(i) for i in range(N)]})
    res = compute_drift(prof, {"z": [float(i) for i in range(N)]})
    assert res["status"] == "unavailable"


def test_insufficient_rows_short_circuits():
    prof = _profile({"a": [float(i) for i in range(N)]})
    res = compute_drift(prof, {"a": [1.0, 2.0, 3.0]})  # < MIN_ROWS
    assert res["status"] == "insufficient_data"
    assert MIN_ROWS >= 30


def test_unmatched_features_are_listed():
    rng = np.random.default_rng(5)
    prof = _profile({"a": rng.normal(size=N).tolist(), "b": rng.normal(size=N).tolist()})
    res = compute_drift(prof, {"a": rng.normal(size=N).tolist()})
    assert res["unmatched_features"] == ["b"]
    assert res["data_drift"]["n_columns"] == 1


def test_bad_schema_version_raises():
    with pytest.raises(ValueError):
        compute_drift({"schema_version": 1, "columns": [], "sample": {}}, {"a": [1.0]})


def test_is_drifted_direction_by_method():
    # p-value test: drift when statistic < threshold.
    assert _is_drifted("K-S p_value", 0.01, 0.05) is True
    assert _is_drifted("K-S p_value", 0.20, 0.05) is False
    # distance test: drift when distance >= threshold.
    assert _is_drifted("Wasserstein distance (normed)", 0.30, 0.1) is True
    assert _is_drifted("Wasserstein distance (normed)", 0.05, 0.1) is False
    assert _is_drifted("K-S p_value", None, 0.05) is None
