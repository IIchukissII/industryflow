# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
`/api/inference/predict`, driven at the wire: what it scores with, what it loads, and what it says
when it declines.

  * the detector is the one the model record names (ADR-0010 dec 6) — not a default the handler
    assumed, which is what every model got while no column carried the choice;
  * an uploaded model is scored at all, from the artifact location it was admitted to (ADR-0030
    dec 8) — before, every upload was refused for having no run;
  * a refusal stays the refusal it is: a 400/404/422 raised inside the handler was re-wrapped as a
    500, telling the caller the platform broke when it had declined;
  * the cold load runs off the event loop.
"""
import asyncio
import os
import sys
import uuid

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import config  # noqa: E402
import extensions.builtins_detectors  # noqa: F401,E402  (registers the built-ins)
import model_cache  # noqa: E402
import model_loader  # noqa: E402
import skops_trust  # noqa: E402
import uploaded_model  # noqa: E402
from routers import inference  # noqa: E402

CID = str(uuid.uuid4())
TOKEN = "tenant_" + CID.replace("-", "_")
ADMITTED = f"s3://mlflow/{TOKEN}/uploads/Abcdefghijklmnop_-123456"
FEATURE_CONFIG = {"base_sensors": ["a"], "transformations": [{"name": "a", "type": "identity", "sensor": "a"}]}


def _on_event_loop():
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class Recorder:
    """A model that records how it was asked, and whose output means whatever its detector says."""

    def __init__(self, output=0.3):
        self.output = output
        self.calls = []

    def predict(self, X):
        self.calls.append("predict")
        return np.array([self.output])

    def decision_function(self, X):
        self.calls.append("decision_function")
        return np.array([0.2])


class FakeRepo:
    def __init__(self, row, feature_config=FEATURE_CONFIG):
        self.row = row
        self.feature_config = feature_config

    async def get_model_by_id(self, company_id, model_id):
        return self.row

    async def get_feature_config_by_id(self, company_id, config_id):
        return self.feature_config


def _row(**over):
    row = {"model_id": "m1", "status": "production", "mlflow_run_id": "run-mine",
           "feature_config_id": "fc1", "detector": "sklearn", "model_version": "1"}
    row.update(over)
    return row


@pytest.fixture
def loads(monkeypatch):
    """What the loader was asked for, and on which thread. The artifact IO itself is stubbed at the
    one call that does it; everything above it — address check, provenance, cache — is real."""
    model_cache.clear()
    seen = []
    model = Recorder()

    def fake_load_pyfunc(uri, provenance):
        seen.append({"uri": uri, "provenance": provenance, "on_loop": _on_event_loop()})
        return model

    monkeypatch.setattr(skops_trust, "load_pyfunc", fake_load_pyfunc)
    monkeypatch.setattr(model_loader, "resolve_model_uri", lambda run_id: f"models:/m-{run_id}")
    monkeypatch.setattr(model_loader, "_run_owned_by", lambda run_id, cid: run_id == "run-mine")
    monkeypatch.setattr(config.config, "UPLOAD_ARTIFACT_BUCKET", "mlflow")
    yield seen, model
    model_cache.clear()


def _client(monkeypatch, row, **repo_kw):
    app = FastAPI()
    app.include_router(inference.router)
    app.state.ml_repository = FakeRepo(row, **repo_kw)

    async def tenant(request, request_data):
        return CID

    monkeypatch.setattr(inference, "_resolve_company_id", tenant)
    return TestClient(app)


def _predict(client):
    return client.post("/api/inference/predict", json={"model_id": "m1", "sensor_data": {"a": 1.0}})


# --- the detector is the record's ----------------------------------------------------------------

def test_the_model_is_scored_by_the_detector_its_record_names(monkeypatch, loads):
    _, model = loads
    r = _predict(_client(monkeypatch, _row(detector="outlier")))
    assert r.status_code == 200, r.text
    assert model.calls == ["decision_function"], "scored by something other than the named detector"


def test_a_record_naming_direct_is_scored_from_its_output(monkeypatch, loads):
    _, model = loads
    r = _predict(_client(monkeypatch, _row(detector="direct")))
    assert r.status_code == 200, r.text
    assert r.json()["prediction"] == pytest.approx(model.output)


def test_a_record_with_no_detector_is_refused_not_defaulted(monkeypatch, loads):
    seen, _ = loads
    r = _predict(_client(monkeypatch, _row(detector=None)))
    assert r.status_code == 422, r.text
    assert seen == [], "an artifact was fetched for a model nothing can interpret"


# --- an uploaded model is scored, from where it was admitted --------------------------------------

def test_an_uploaded_model_is_scored_from_its_admitted_artifact(monkeypatch, loads):
    seen, _ = loads
    row = _row(mlflow_run_id=None, artifact_uri=ADMITTED, provenance="uploaded", detector="outlier")
    r = _predict(_client(monkeypatch, row))
    assert r.status_code == 200, r.text
    assert seen[0]["uri"] == ADMITTED
    assert seen[0]["provenance"] == uploaded_model.PROVENANCE_UPLOADED


def test_a_row_carrying_a_staged_artifact_is_not_loaded(monkeypatch, loads):
    """A row registered before the admission check existed carries whatever it was given. Serving
    checks the address on every cold load, not only at the gates."""
    seen, _ = loads
    staged = f"s3://mlflow/_upload-staging/{TOKEN}/Abcdefghijklmnop_-123456"
    row = _row(mlflow_run_id=None, artifact_uri=staged, provenance="uploaded", detector="outlier")
    r = _predict(_client(monkeypatch, row))
    assert r.status_code == 422, r.text
    assert seen == []


def test_a_row_carrying_another_tenants_run_is_not_loaded(monkeypatch, loads):
    seen, _ = loads
    r = _predict(_client(monkeypatch, _row(mlflow_run_id="run-theirs")))
    assert r.status_code == 422, r.text
    assert seen == []


def test_a_kernel_model_is_loaded_with_kernel_provenance(monkeypatch, loads):
    seen, _ = loads
    assert _predict(_client(monkeypatch, _row())).status_code == 200
    assert seen[0]["uri"] == "models:/m-run-mine"
    assert seen[0]["provenance"] == uploaded_model.PROVENANCE_KERNEL


def test_the_cold_load_runs_off_the_event_loop(monkeypatch, loads):
    seen, _ = loads
    _predict(_client(monkeypatch, _row()))
    assert seen[0]["on_loop"] is False, "the artifact was fetched on the event loop"


# --- a refusal is not a crash --------------------------------------------------------------------

def test_a_missing_feature_config_is_a_404_not_a_500(monkeypatch, loads):
    r = _predict(_client(monkeypatch, _row(), feature_config=None))
    assert r.status_code == 404, r.text


def test_a_model_without_a_feature_config_is_a_400_not_a_500(monkeypatch, loads):
    r = _predict(_client(monkeypatch, _row(feature_config_id=None)))
    assert r.status_code == 400, r.text


def test_an_uninterpretable_output_is_a_422_not_a_500(monkeypatch, loads):
    _, model = loads
    model.output = 7.5   # declared a 0-1 score, and is not one
    r = _predict(_client(monkeypatch, _row(detector="direct")))
    assert r.status_code == 422, r.text


def test_a_genuine_fault_is_still_a_500(monkeypatch, loads):
    _, model = loads

    def broken(X):
        raise RuntimeError("boom")

    model.decision_function = broken
    r = _predict(_client(monkeypatch, _row(detector="outlier")))
    assert r.status_code == 500


# --- the address is checked on every load, warm hits included (ADR-0030 dec 8) ------------

def test_a_policy_tightening_refuses_an_already_warm_upload(monkeypatch, loads):
    """A cached model is not a standing admission: once the uploads bucket the deployment admits
    changes, the warm copy is refused at once rather than served until it falls out of the cache."""
    seen, _ = loads
    row = _row(mlflow_run_id=None, artifact_uri=ADMITTED, provenance="uploaded", detector="outlier")
    client = _client(monkeypatch, row)
    assert _predict(client).status_code == 200
    assert len(seen) == 1 and model_cache.info()["size"] == 1, "the first call did not warm the cache"

    monkeypatch.setattr(config.config, "UPLOAD_ARTIFACT_BUCKET", "admitted-elsewhere")
    r = _predict(client)
    assert r.status_code == 422, r.text
    assert len(seen) == 1, "the refusal came from a cold load, not from the check on the hit"


def test_a_warm_hit_still_runs_the_address_check(monkeypatch, loads):
    seen, _ = loads
    client = _client(monkeypatch, _row())
    assert _predict(client).status_code == 200

    checked = []
    real = model_loader.check_address
    monkeypatch.setattr(model_loader, "check_address", lambda **kw: (checked.append(kw), real(**kw)))
    assert _predict(client).status_code == 200
    assert len(seen) == 1, "the second call was not a warm hit"
    assert checked == [{"company_id": CID, "mlflow_run_id": "run-mine", "artifact_uri": None}]
