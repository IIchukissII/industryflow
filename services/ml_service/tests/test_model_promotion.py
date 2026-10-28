# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A notebook-registered version becomes a platform model by promotion (ADR-0040).

The real router and registration gate, with the tracking store replaced by a fake that holds one
tenant's model and run; the gate's own checks are stubbed as in test_model_admission.
"""
import json
import os
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mlflow.exceptions import MlflowException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import auth  # noqa: E402
import drift  # noqa: E402
import extensions.builtins_detectors  # noqa: F401,E402  (registers the built-ins)
import model_compat  # noqa: E402
import model_loader  # noqa: E402
import skops_trust  # noqa: E402
from mlflow_namespace import tenant_prefix  # noqa: E402
from routers import registered_models  # noqa: E402
from routers.models import get_company_id_dependency  # noqa: E402

CID = str(uuid.uuid4())
OWN = tenant_prefix(CID) + "vpd-regressor"
SAMPLE = drift.build_reference_profile({"a": [float(v) for v in range(40)]})
SAFE_FLAVORS = {"python_function": {"loader_module": "mlflow.sklearn"},
                "sklearn": {"serialization_format": "skops"}}


class FakeMlflow:
    def __init__(self, sample_bytes):
        self.sample_bytes = sample_bytes
        self.downloads = 0

    def get_model_version(self, name, version):
        if name != OWN or version != "3":
            raise MlflowException("RESOURCE_DOES_NOT_EXIST")
        return SimpleNamespace(run_id="run-mine", description=None)

    def get_run(self, run_id):
        return SimpleNamespace(info=SimpleNamespace(experiment_id="7"),
                               data=SimpleNamespace(metrics={"mae": 0.4}))

    def get_registered_model(self, name):
        return SimpleNamespace(description="forecasts VPD")

    def list_artifacts(self, run_id, path):
        if self.sample_bytes is None:
            return []
        return [SimpleNamespace(path=drift.FEATURE_SAMPLE_ARTIFACT, is_dir=False,
                                file_size=len(self.sample_bytes))]

    def download_artifacts(self, run_id, path, dst):
        self.downloads += 1
        local = os.path.join(dst, "feature_sample.json")
        with open(local, "wb") as fh:
            fh.write(self.sample_bytes)
        return local


class FakeRepo:
    def __init__(self):
        self.created = []

    async def create_model(self, company_id, model_data):
        self.created.append(model_data)
        return uuid.uuid4()

    async def get_feature_config_by_id(self, company_id, config_id):
        return {"id": config_id, "base_sensors": ["a"], "transformations": []} if config_id == "fc1" else None

    async def sensor_ids_by_name(self, company_id, equipment_id, names):
        return {"a": "11111111-1111-1111-1111-111111111111"}


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(mlflow=FakeMlflow(json.dumps(SAMPLE).encode()), role="engineer")
    app = FastAPI()
    app.include_router(registered_models.router)
    app.dependency_overrides[get_company_id_dependency] = lambda: CID
    app.dependency_overrides[auth.verify_jwt_token] = lambda: {"user_id": "u", "payload": {}, "role": state.role}
    app.state.ml_repository = FakeRepo()
    monkeypatch.setattr(registered_models, "_client", lambda: state.mlflow)
    monkeypatch.setattr(model_loader, "_run_owned_by", lambda run_id, company_id: company_id == CID)
    monkeypatch.setattr(model_loader, "resolve_model_uri", lambda run_id: f"runs:/{run_id}/model")
    monkeypatch.setattr(skops_trust, "read_flavors", lambda uri: SAFE_FLAVORS)

    async def compatible(_):
        return model_compat.Compatibility(status=model_compat.COMPATIBLE, flavor="mlflow.sklearn")

    monkeypatch.setattr(model_compat, "evaluate_run", compatible)
    monkeypatch.setattr(model_compat, "evaluate_uri", compatible)
    state.client = TestClient(app)
    state.repo = app.state.ml_repository
    return state


def _promote(world, name="vpd-regressor", version="3", **body):
    return world.client.post(f"/api/registered-models/{name}/versions/{version}/promote",
                             json={"feature_config_id": "fc1", **body})


def test_a_version_is_promoted_with_its_runs_sample(world):
    r = _promote(world, equipment_id="22222222-2222-2222-2222-222222222222")
    assert r.status_code == 201, r.text
    row = world.repo.created[0]
    assert (row["model_name"], row["model_version"], row["mlflow_run_id"]) == ("vpd-regressor", "3", "run-mine")
    assert row["provenance"] == "kernel" and row["detector"] == "sklearn"
    assert row["reference_profile"]["columns"] == ["a"] and "refused" not in row["reference_profile"]
    assert row["sensor_ids"] == ["11111111-1111-1111-1111-111111111111"]
    assert row["description"] == "forecasts VPD" and row["training_metrics"] == {"mae": 0.4}
    assert r.json()["baseline"]["data_drift"] is True


def test_another_tenants_or_a_missing_version_is_not_found(world):
    assert _promote(world, name="other").status_code == 404
    assert _promote(world, version="4").status_code == 404
    assert world.repo.created == []


def test_a_version_number_is_digits(world):
    assert _promote(world, version="3x").status_code == 400


def test_an_observer_cannot_promote(world):
    world.role = "observer"
    assert _promote(world).status_code == 403
    assert world.repo.created == []


def test_a_run_without_a_sample_is_promoted_without_a_baseline(world):
    world.mlflow.sample_bytes = None
    r = _promote(world)
    assert r.status_code == 201, r.text
    assert world.repo.created[0]["reference_profile"] is None
    assert "no feature sample" in r.json()["baseline"]["reason"]


def test_an_oversized_sample_is_refused_unread(world, monkeypatch):
    monkeypatch.setattr(drift, "MAX_SAMPLE_BYTES", 10)
    r = _promote(world)
    assert r.status_code == 201, r.text
    assert world.mlflow.downloads == 0
    assert "are not read" in r.json()["baseline"]["reason"]


def test_a_sample_that_is_not_json_is_refused(world):
    world.mlflow.sample_bytes = b"\x80not json"
    r = _promote(world)
    assert r.status_code == 201, r.text
    assert "not JSON" in r.json()["baseline"]["reason"]


def test_the_feature_configuration_must_be_the_tenants(world):
    r = _promote(world, feature_config_id="nope")
    assert r.status_code == 422, r.text
    assert world.repo.created == []
