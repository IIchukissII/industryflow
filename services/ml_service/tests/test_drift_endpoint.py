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
import json
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
from drift import align_readings, build_reference_profile, compute_drift, with_derived_scores  # noqa: E402
import scoring  # noqa: E402
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


def test_a_value_that_is_not_a_finite_number_did_not_report():
    # One bad value drops its instant like a missed reading, not the whole evaluation (ADR-0021 dec 1).
    readings = [_reading(i, "a", float(i)) for i in range(5)] + [_reading(i, "b", 1.0) for i in range(5)]
    readings[1]["value"] = None
    readings[2]["value"] = float("nan")
    readings[3]["value"] = float("inf")
    times, rows = align_readings(readings, ["a", "b"])
    assert times == [T0, T0 + timedelta(seconds=4)]
    assert rows["a"] == [0.0, 4.0]


def test_unaligned_columns_are_refused_rather_than_guessed_at():
    prof = build_reference_profile({"a": list(range(50)), "b": list(range(50))})
    with pytest.raises(ValueError, match="row-aligned"):
        compute_drift(prof, {"a": [1.0] * 40, "b": [1.0] * 35})


def test_current_predictions_are_their_own_sample():
    rng = np.random.default_rng(0)
    prof = with_derived_scores(build_reference_profile({"a": rng.normal(size=N).tolist()}),
                               rng.uniform(0, 0.2, size=N).tolist(), derived_at="t")
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
    )
    if prediction:
        profile = with_derived_scores(profile, rng.uniform(0, 0.2, size=N).tolist(), derived_at="t")
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

    def run(model, readings, feature_config=FEATURES, **state):
        app = FastAPI()
        app.include_router(drift_router.router)
        app.state.ml_repository = FakeRepo(model, readings, feature_config)
        for name, value in state.items():
            setattr(app.state, name, value)

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
    assert "derived when the model is deployed" in res["prediction_drift_reason"]


def test_a_model_nothing_can_interpret_still_reports_data_drift(evaluate):
    rng = np.random.default_rng(4)
    model = _model(rng)
    model["detector"] = None
    res = evaluate(model, _fleet(rng))
    assert res["status"] == "ok"
    assert res["prediction_drift"] is None
    assert "cannot be scored" in res["prediction_drift_reason"]


def test_a_configuration_naming_an_unknown_transform_still_reports_data_drift(evaluate):
    # Stored before such a configuration was refused at write: no prediction drift, the reason, and the
    # data drift that reads only the sensors.
    rng = np.random.default_rng(5)
    stale = {"base_sensors": ["a", "b"], "transformations": [{"name": "a", "type": "raw", "sensor": "a"},
                                                             {"name": "b", "type": "identity", "sensor": "b"}]}
    res = evaluate(_model(rng), _fleet(rng), feature_config=stale)
    assert res["status"] == "ok", res
    assert res["data_drift"]["n_columns"] == 2
    assert res["prediction_drift"] is None
    assert "raw" in res["prediction_drift_reason"]


def test_a_reference_of_labels_is_not_a_prediction_baseline(evaluate, monkeypatch):
    """A stored profile whose prediction column holds labels, not detector scores (ADR-0021
    dec 4): prediction drift is refused with the reason, nothing is scored against it, and data
    drift — which compares inputs — is still reported."""
    rng = np.random.default_rng(6)
    model = _model(rng)
    model["reference_profile"]["sample"]["prediction_score"] = rng.choice([-1.0, 1.0], size=N).tolist()
    scored = []
    monkeypatch.setattr(scoring, "scores_blocking", lambda *a: scored.append(a) or [])
    res = evaluate(model, _fleet(rng))
    assert res["status"] == "ok", res
    assert res["data_drift"]["n_columns"] == 2
    assert res["prediction_drift"] is None
    assert "outside the detector's declared scale" in res["prediction_drift_reason"]
    assert scored == [], "the window was scored against a profile that is not a baseline"


def test_compute_refuses_prediction_drift_on_an_off_scale_reference():
    rng = np.random.default_rng(7)
    prof = with_derived_scores(build_reference_profile({"a": rng.normal(size=N).tolist()}),
                               rng.uniform(0, 0.2, size=N).tolist(), derived_at="t")
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


def test_the_deploy_gate_says_why_a_sample_on_an_unknown_transform_is_not_scored():
    import baseline_gate

    class Repo:
        async def get_feature_config_by_id(self, company_id, config_id):
            return {"base_sensors": ["a", "b"],
                    "transformations": [{"name": "a", "type": "raw", "sensor": "a"},
                                        {"name": "b", "type": "identity", "sensor": "b"}]}

    rng = np.random.default_rng(7)
    model = {"reference_profile": build_reference_profile({"a": rng.normal(size=N).tolist(),
                                                           "b": rng.normal(size=N).tolist()}),
             "feature_config_id": "fc1", "equipment_id": str(uuid.uuid4())}
    planned = asyncio.run(baseline_gate.plan(object(), Repo(), CID, model))
    assert planned.rows == [] and "raw" in planned.reason


DRIFT_CONTEXTS = []


def _drift_recorder():
    from extensions import DetectionResult, get_detector, register_detector

    if get_detector("drift_ctx_recorder") is None:
        @register_detector("drift_ctx_recorder")
        async def recorder(features, model, threshold, ctx):
            DRIFT_CONTEXTS.append(ctx)
            return DetectionResult(score=0.9, is_anomaly=True)


def test_prediction_drift_scores_with_the_feature_names_in_the_context(evaluate):
    # ADR-0010 dec 6: drift calls the detector with the same context inference does.
    _drift_recorder()
    DRIFT_CONTEXTS.clear()
    rng = np.random.default_rng(3)
    model = dict(_model(rng), detector="drift_ctx_recorder", training_metrics={"reconstruction_scale": 0.5})
    evaluate(model, _fleet(rng))
    assert DRIFT_CONTEXTS, "the detector was never called"
    assert {tuple(c.feature_names) for c in DRIFT_CONTEXTS} == {("a", "b")}
    assert {c.equipment_id for c in DRIFT_CONTEXTS} == {model["equipment_id"]}
    assert {c.reconstruction_scale for c in DRIFT_CONTEXTS} == {0.5}


def test_the_deploy_gate_scores_with_the_feature_names_in_the_context():
    import baseline_gate

    class Repo:
        async def get_feature_config_by_id(self, company_id, config_id):
            return FEATURES

    rng = np.random.default_rng(8)
    model = {"reference_profile": build_reference_profile({"a": rng.normal(size=N).tolist(),
                                                           "b": rng.normal(size=N).tolist()}),
             "feature_config_id": "fc1", "equipment_id": "eq-2", "detector": "direct",
             "training_metrics": '{"reconstruction_scale": 0.75}'}
    planned = asyncio.run(baseline_gate.plan(object(), Repo(), CID, model))
    assert planned.reason is None, planned.reason
    assert planned.spec == scoring.Scoring("direct", ["a", "b"], "eq-2", 0.75)
    # The same scoring travels to the isolated probe as JSON.
    sent = json.loads(json.dumps(planned.for_probe()["scoring"]))
    assert scoring.Scoring.from_json(sent) == planned.spec


# --- stateful features in drift (ADR-0044 dec 3, ADR-0023 dec 9) ---------------------------------

STATEFUL = {"base_sensors": ["a", "b"],
            "transformations": [{"name": "a", "type": "identity", "sensor": "a"},
                                {"name": "a_dev", "type": "statistical", "sensor": "a",
                                 "params": {"stat_type": "deviation_from_window_mean", "granularity": "1min"}}]}


class AsOfProvider:
    """Records the instant each baseline is read as of; ``mean=None`` is a sensor with no window yet."""

    def __init__(self, mean=0.0):
        self.mean, self.as_of = mean, []

    async def compute_baseline(self, company_id, equipment_id, sensor_name, granularity, as_of=None):
        self.as_of.append(as_of)
        return None if self.mean is None else {"mean": self.mean, "std": 1.0}


class Switch:
    def __init__(self, on):
        self.on = on

    async def enabled(self):
        return self.on


def test_each_row_reads_the_window_written_by_its_own_instant(evaluate):
    rng = np.random.default_rng(11)
    provider = AsOfProvider()
    res = evaluate(_model(rng), _fleet(rng), feature_config=STATEFUL, baseline_provider=provider)
    assert res["prediction_drift"] is not None, res.get("prediction_drift_reason")
    instants = {_reading(i, "a", 0.0)["time"] for i in range(0, 2 * N, 2)}
    assert provider.as_of and set(provider.as_of) <= instants and None not in provider.as_of


@pytest.mark.parametrize("state, why", [
    ({"baseline_provider": AsOfProvider(mean=None)}, "no closed aggregate window"),
    ({"baseline_provider": AsOfProvider(), "stateful_switch": Switch(False)}, "switched off"),
    ({}, "no baseline source"),
])
def test_a_substitute_value_leaves_prediction_drift_not_computed(evaluate, monkeypatch, state, why):
    """A score on a substitute is one the model was not given; compared with the baseline, it would
    read as drift or hide it (ADR-0044 dec 3). Data drift reads the sensors and still reports."""
    rng = np.random.default_rng(12)
    scored = []
    monkeypatch.setattr(scoring, "scores_blocking", lambda *a: scored.append(a) or [])
    res = evaluate(_model(rng), _fleet(rng), feature_config=STATEFUL, **state)
    assert res["status"] == "ok", res
    assert res["data_drift"]["n_columns"] == 2
    assert res["prediction_drift"] is None
    assert "substitute" in res["prediction_drift_reason"] and why in res["prediction_drift_reason"]
    assert scored == []


def test_the_deploy_gate_derives_no_baseline_for_stateful_features():
    """A sample row carries no instant, so no window can be read as of it (ADR-0023 dec 9)."""
    import baseline_gate

    class Repo:
        async def get_feature_config_by_id(self, company_id, config_id):
            return STATEFUL

    class State:
        baseline_provider = AsOfProvider()

    rng = np.random.default_rng(13)
    model = {"reference_profile": build_reference_profile({"a": rng.normal(size=N).tolist(),
                                                           "b": rng.normal(size=N).tolist()}),
             "feature_config_id": "fc1", "equipment_id": str(uuid.uuid4()), "detector": "direct"}
    planned = asyncio.run(baseline_gate.plan(State(), Repo(), CID, model))
    assert planned.rows == [] and "carry no time" in planned.reason and "a_dev" in planned.reason
    assert State.baseline_provider.as_of == [], "no baseline was read for a row it cannot belong to"
