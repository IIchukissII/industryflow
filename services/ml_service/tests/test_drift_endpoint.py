# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The drift endpoint, end to end over fakes of its IO (ADR-0021 dec 1, 3).

Two things it got wrong, both silently:

  * the window was grouped into per-sensor lists with the times thrown away. Sensors reporting at
    different rates produced columns of different lengths, the frame could not be built, and the
    model reported "unavailable" — every time, for any real fleet.
  * prediction drift was never computed: the reference could carry the model's scores, but nothing
    produced the CURRENT scores, so half of ADR-0021 dec 1 returned None forever.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import extensions.builtins_detectors  # noqa: F401,E402  (registers the built-ins)
from drift import align_readings, build_reference_profile, compute_drift  # noqa: E402
from routers import drift as drift_router  # noqa: E402

CID = str(uuid.uuid4())
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
N = 300


# --- alignment ----------------------------------------------------------------------------------

def _reading(i, name, value):
    return {"time": T0 + timedelta(seconds=i), "sensor_name": name, "value": value}


def test_sensors_at_different_rates_align_on_the_instants_they_share():
    readings = [_reading(i, "fast", float(i)) for i in range(10)]
    readings += [_reading(i, "slow", float(-i)) for i in range(0, 10, 2)]
    times, rows = align_readings(readings, ["fast", "slow"])
    assert len(times) == 5
    assert rows["fast"] == [0.0, 2.0, 4.0, 6.0, 8.0]
    assert rows["slow"] == [0.0, -2.0, -4.0, -6.0, -8.0]


def test_sensors_not_compared_do_not_thin_the_rows():
    readings = [_reading(i, "a", 1.0) for i in range(6)] + [_reading(0, "other", 9.0)]
    _, rows = align_readings(readings, ["a"])
    assert len(rows["a"]) == 6


def test_unaligned_columns_are_refused_rather_than_guessed_at():
    prof = build_reference_profile({"a": list(range(50)), "b": list(range(50))})
    with pytest.raises(ValueError, match="row-aligned"):
        compute_drift(prof, {"a": [1.0] * 40, "b": [1.0] * 35})


def test_current_predictions_are_their_own_sample():
    rng = np.random.default_rng(0)
    prof = build_reference_profile({"a": rng.normal(size=N).tolist()},
                                   prediction_scores=rng.uniform(0, 0.2, size=N).tolist())
    res = compute_drift(prof, {"a": rng.normal(size=N).tolist()},
                        current_predictions=rng.uniform(0.7, 1.0, size=120).tolist())
    assert res["prediction_drift"]["drifted"] is True


# --- the endpoint -------------------------------------------------------------------------------

class ScoreModel:
    """Emits a 0-1 score that is high for every reading — the model's output has moved."""

    def predict(self, X):
        return np.array([0.7 + 0.25 * (abs(float(np.asarray(X)[0][0])) % 1.0)])


class FakeRepo:
    def __init__(self, model, readings, feature_config):
        self.model, self.readings, self.feature_config = model, readings, feature_config

    async def get_model_by_id(self, company_id, model_id):
        return self.model

    async def read_sensor_window(self, company_id, sensor_ids, start, end):
        return self.readings

    async def get_feature_config_by_id(self, company_id, config_id):
        return self.feature_config


def _fleet(rng):
    """Two sensors at different rates — every second and every other second."""
    readings = [_reading(i, "a", float(rng.normal())) for i in range(2 * N)]
    readings += [_reading(i, "b", float(rng.normal(5, 2))) for i in range(0, 2 * N, 2)]
    return readings


def _model(rng, prediction=True):
    profile = build_reference_profile(
        {"a": rng.normal(size=N).tolist(), "b": rng.normal(5, 2, size=N).tolist()},
        prediction_scores=rng.uniform(0, 0.2, size=N).tolist() if prediction else None,
    )
    return {"model_id": "m1", "reference_profile": profile, "sensor_ids": [str(uuid.uuid4())],
            "feature_config_id": "fc1", "detector": "direct", "mlflow_run_id": "run-1",
            "equipment_id": str(uuid.uuid4())}


FEATURES = {"base_sensors": ["a", "b"],
            "transformations": [{"name": "a", "type": "identity", "sensor": "a"},
                                {"name": "b", "type": "identity", "sensor": "b"}]}


@pytest.fixture
def evaluate(monkeypatch):
    threads = {}

    async def loaded(company_id, model):
        return ScoreModel()

    real_compute = drift_router.compute_drift

    def compute(*a, **kw):
        try:
            asyncio.get_running_loop()
            threads["compute_on_loop"] = True
        except RuntimeError:
            threads["compute_on_loop"] = False
        return real_compute(*a, **kw)

    monkeypatch.setattr(drift_router, "load_model_or_refuse", loaded)
    monkeypatch.setattr(drift_router, "compute_drift", compute)

    def run(model, readings, feature_config=FEATURES):
        app = FastAPI()
        app.include_router(drift_router.router)
        app.state.ml_repository = FakeRepo(model, readings, feature_config)

        async def tenant(request, request_data):
            return CID

        monkeypatch.setattr(drift_router, "_resolve_company_id", tenant)
        r = TestClient(app).post("/api/drift/evaluate", json={"model_id": "m1"})
        assert r.status_code == 200, r.text
        return r.json()

    run.threads = threads
    return run


def test_sensors_at_different_rates_are_evaluated_not_unavailable(evaluate):
    rng = np.random.default_rng(1)
    res = evaluate(_model(rng), _fleet(rng))
    assert res["status"] == "ok", res
    assert res["n_current_rows"] == N   # the instants at which both sensors reported


def test_prediction_drift_is_computed_from_the_current_window(evaluate):
    rng = np.random.default_rng(2)
    res = evaluate(_model(rng), _fleet(rng))
    assert res["prediction_drift"] is not None, res.get("prediction_drift_reason")
    assert res["prediction_drift"]["drifted"] is True


def test_without_a_prediction_baseline_it_says_why(evaluate):
    rng = np.random.default_rng(3)
    res = evaluate(_model(rng, prediction=False), _fleet(rng))
    assert res["prediction_drift"] is None
    assert "captured no prediction scores" in res["prediction_drift_reason"]


def test_a_model_nothing_can_interpret_still_reports_data_drift(evaluate):
    rng = np.random.default_rng(4)
    model = _model(rng)
    model["detector"] = None
    res = evaluate(model, _fleet(rng))
    assert res["status"] == "ok"
    assert res["prediction_drift"] is None
    assert "cannot be scored" in res["prediction_drift_reason"]


def test_a_reference_of_labels_is_not_a_prediction_baseline(evaluate, monkeypatch):
    """A stored profile whose prediction column holds labels, not detector scores (ADR-0021
    dec 4): prediction drift is refused with the reason, nothing is scored against it, and data
    drift — which compares inputs — is still reported."""
    rng = np.random.default_rng(6)
    model = _model(rng)
    model["reference_profile"]["sample"]["prediction_score"] = rng.choice([-1.0, 1.0], size=N).tolist()
    scored = []
    monkeypatch.setattr(drift_router, "_score_rows", lambda *a: scored.append(a) or [])
    res = evaluate(model, _fleet(rng))
    assert res["status"] == "ok", res
    assert res["data_drift"]["n_columns"] == 2
    assert res["prediction_drift"] is None
    assert "outside the detector's declared scale" in res["prediction_drift_reason"]
    assert scored == [], "the window was scored against a profile that is not a baseline"


def test_compute_refuses_prediction_drift_on_an_off_scale_reference():
    rng = np.random.default_rng(7)
    prof = build_reference_profile({"a": rng.normal(size=N).tolist()},
                                   prediction_scores=rng.uniform(0, 0.2, size=N).tolist())
    prof["sample"]["prediction_score"] = (rng.uniform(0, 0.2, size=N) * 40).tolist()
    res = compute_drift(prof, {"a": rng.normal(size=N).tolist()},
                        current_predictions=rng.uniform(0, 0.2, size=120).tolist())
    assert res["status"] == "ok"
    assert res["prediction_drift"] is None
    assert "not a baseline" in res["prediction_drift_reason"]


def test_evidently_runs_off_the_event_loop(evaluate):
    rng = np.random.default_rng(5)
    evaluate(_model(rng), _fleet(rng))
    assert evaluate.threads["compute_on_loop"] is False
