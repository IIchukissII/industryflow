# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The feature sample a notebook logs for its model's drift baseline (ADR-0040 dec 4)."""
import json
import math
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from industryflow import FEATURE_SAMPLE_ARTIFACT, feature_sample, log_feature_sample


def test_a_dataframe_becomes_its_columns_sample():
    s = feature_sample(pd.DataFrame({"temp": [1.0, 2.0, float("nan")], "humidity": [4, 5, 6]}))
    assert s["columns"] == ["temp", "humidity"] and s["prediction_column"] is None
    assert s["sample"] == {"temp": [1.0, 2.0, None], "humidity": [4.0, 5.0, 6.0]}
    assert s["n_training_rows"] == 3 and s["n_samples"] == 3
    json.dumps(s)


def test_long_data_is_evenly_down_sampled():
    s = feature_sample({"x": list(range(10_000))}, max_sample_rows=100)
    assert s["n_training_rows"] == 10_000 and s["n_samples"] == 100
    assert s["sample"]["x"][0] == 0.0 and s["sample"]["x"][-1] == 9999.0


@pytest.mark.parametrize("data", [{}, {"a": []}, {"a": [1.0], "b": [1.0, 2.0]}, [1.0, 2.0]])
def test_data_that_is_not_aligned_columns_is_refused(data):
    with pytest.raises(ValueError):
        feature_sample(data)


def test_the_sample_is_logged_into_the_active_run(monkeypatch):
    logged = []
    fake = SimpleNamespace(active_run=lambda: object(), log_dict=lambda d, path: logged.append((d, path)))
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    s = log_feature_sample({"a": [1.0, math.inf]})
    assert logged == [(s, FEATURE_SAMPLE_ARTIFACT)]
    assert s["sample"]["a"] == [1.0, None]


def test_without_a_run_nothing_is_logged(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlflow", SimpleNamespace(active_run=lambda: None))
    with pytest.raises(RuntimeError, match="no active MLflow run"):
        log_feature_sample({"a": [1.0]})
