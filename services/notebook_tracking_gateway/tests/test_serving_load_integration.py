# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
End-to-end proof that a model logged THROUGH the gateway loads on the trusted side.

A kernel logs through the gateway (ADR-0019), which stores every artifact under the tenant prefix.
The serving side loads models from the tracking server directly, using the location MLflow
recorded. If the recorded location and the stored location disagree, the model logs fine and
every load 404s. The other gateway proofs drive raw HTTP and never load from the trusted side, so
that disagreement was invisible to CI.

This test logs a real sklearn model with the MLflow client, authenticated by a tracking capability,
and then loads it straight from the tracking server as serving does. It also checks that a
caller-chosen artifact location is not what MLflow records.

IMPORTANT: run from INSIDE the compose network (minio:9000 and mlflow:5000 must resolve). SKIPS when
the stack is unreachable; IF_REQUIRE_LIVE_STACK (conftest.py) turns that skip into a failure in CI.
"""
import json
import os
import uuid

import pytest

requests = pytest.importorskip("requests")
redis = pytest.importorskip("redis")

pytestmark = pytest.mark.integration

_KEY_PREFIX = "nbcap:"


def _gw():
    return os.getenv("TRACKING_GATEWAY_URL", "http://notebook-tracking-gateway:5050").rstrip("/")


def _direct():
    return os.getenv("MLFLOW_DIRECT_URL", "http://mlflow:5000").rstrip("/")


def _tenant_prefix(company_id):
    return "tenant_" + str(uuid.UUID(company_id)).replace("-", "_") + "/"


@pytest.fixture(scope="module")
def handle_for():
    try:
        requests.get(f"{_gw()}/health", timeout=5).raise_for_status()
        requests.get(f"{_direct()}/health", timeout=5).raise_for_status()
        rc = redis.Redis.from_url(os.getenv("TRACKING_REDIS_URL", "redis://redis:6379/0"))
        rc.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"live stack unreachable: {exc}")

    def mint(company_id):
        h = "e2e-" + uuid.uuid4().hex
        rc.set(f"{_KEY_PREFIX}{h}", json.dumps(
            {"user": "e2e", "company_id": company_id, "audience": "tracking"}), ex=600)
        return h

    return mint


def _direct_experiment(exp_id):
    r = requests.get(f"{_direct()}/api/2.0/mlflow/experiments/get",
                     params={"experiment_id": exp_id}, timeout=10)
    r.raise_for_status()
    return r.json()["experiment"]


def test_a_caller_chosen_artifact_location_is_not_recorded(handle_for):
    cid, other = str(uuid.uuid4()), str(uuid.uuid4())
    h = handle_for(cid)
    r = requests.post(f"{_gw()}/api/2.0/mlflow/experiments/create", timeout=10,
                      headers={"Authorization": f"Bearer {h}"},
                      json={"name": "loc-" + uuid.uuid4().hex,
                            "artifact_location": "mlflow-artifacts:/" + _tenant_prefix(other) + "x"})
    assert r.status_code == 200, r.text
    loc = _direct_experiment(r.json()["experiment_id"])["artifact_location"]
    assert loc.startswith("mlflow-artifacts:/" + _tenant_prefix(cid)), loc


def test_a_gateway_logged_model_loads_from_the_tracking_server(handle_for, monkeypatch):
    mlflow = pytest.importorskip("mlflow")
    np = pytest.importorskip("numpy")
    from sklearn.linear_model import LinearRegression

    cid = str(uuid.uuid4())
    X = np.arange(20, dtype=float).reshape(-1, 1)
    model = LinearRegression().fit(X, 2 * X.ravel() + 1)

    # The kernel side: through the gateway, authenticated by the tracking capability.
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", handle_for(cid))
    mlflow.set_tracking_uri(_gw())
    exp = mlflow.set_experiment("serve-" + uuid.uuid4().hex)
    with mlflow.start_run():
        info = mlflow.sklearn.log_model(model, name="model", input_example=X[:2])
    assert info.model_id

    # The trusted side: straight to the tracking server, as ml_service loads a deployed model.
    monkeypatch.delenv("MLFLOW_TRACKING_TOKEN")
    mlflow.set_tracking_uri(_direct())
    assert _direct_experiment(exp.experiment_id)["artifact_location"].startswith(
        "mlflow-artifacts:/" + _tenant_prefix(cid))
    loaded = mlflow.pyfunc.load_model(f"models:/{info.model_id}")
    assert np.allclose(loaded.predict(X), model.predict(X))
