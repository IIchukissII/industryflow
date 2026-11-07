# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Promotion of a notebook-registered version against a live stack (ADR-0040, ADR-0035 dec 4).

The unit tests fake MLflow and the artifact read, so a wrong registry call or artifact path would
leave them green, and a sample read that finds nothing promotes the model with no drift baseline and
no refusal. Here the kernel's half runs as a kernel does: the platform client's sample helper and
MLflow's own logging, through the tracking gateway on a tracking capability; the promotion is this
service's real endpoint reading the real tracking server.

IMPORTANT: run from INSIDE the compose network. SKIPS when the stack is unreachable;
IF_REQUIRE_LIVE_STACK (conftest.py) turns that skip into a failure in CI.
"""
import json
import os
import uuid

import pytest

import requests
import pandas as pd
import redis
import mlflow
import mlflow.sklearn as mlflow_sklearn
import sklearn.linear_model as linear_model
import industryflow

pytestmark = pytest.mark.integration

FEATURES = ["temperature", "vibration"]


def _ml():
    return os.getenv("ML_SERVICE_URL", "http://ml-service-api:8002").rstrip("/")


def _api():
    return os.getenv("API_GATEWAY_URL", "http://api-gateway:8000").rstrip("/")


def _gw():
    return os.getenv("TRACKING_GATEWAY_URL", "http://notebook-tracking-gateway:5050").rstrip("/")


@pytest.fixture(scope="module")
def session():
    email, password = os.getenv("IF_UPLOADER_EMAIL"), os.getenv("IF_UPLOADER_PASSWORD")
    company_id = os.getenv("IF_UPLOADER_COMPANY_ID")
    if not (email and password and company_id):
        pytest.skip("IF_UPLOADER_EMAIL / IF_UPLOADER_PASSWORD / IF_UPLOADER_COMPANY_ID not set")
    try:
        for url in (_ml(), _api(), _gw()):
            requests.get(f"{url}/health", timeout=5).raise_for_status()
        store = redis.Redis.from_url(os.getenv("TRACKING_REDIS_URL", "redis://redis:6379/0"))
        store.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"live stack unreachable: {exc}")
    r = requests.post(f"{_api()}/auth/jwt/login", timeout=30, data={"username": email, "password": password})
    assert r.status_code == 200, f"login -> {r.status_code} {r.text[:200]}"
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = requests.post(f"{_ml()}/api/feature-configs", headers=headers, timeout=30, json={
        "equipment_type": "pump", "name": f"ci-promote-{uuid.uuid4().hex[:8]}", "base_sensors": FEATURES,
        "transformations": [{"name": f"{s}_raw", "type": "identity", "sensor": s} for s in FEATURES],
    })
    assert r.status_code in (200, 201), f"feature config -> {r.status_code} {r.text[:300]}"
    return {"headers": headers, "company_id": company_id, "store": store, "config": r.json()["id"]}


def _kernel_run(session, monkeypatch, *, sample=None, raw_sample=None):
    """Train and register through the gateway as a kernel does; returns the registered name."""
    # The spawner's mint, as the hub writes it (ADR-0015 dec 1): one user, one tenant, the tracking plane.
    handle = "ci-" + uuid.uuid4().hex
    session["store"].set(f"nbcap:{handle}", json.dumps(
        {"user": "ci-promoter", "company_id": session["company_id"], "audience": "tracking"}), ex=600)
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", handle)
    mlflow.set_tracking_uri(_gw())
    mlflow.set_experiment("ci-promote-" + uuid.uuid4().hex[:8])
    X = pd.DataFrame({"temperature": [20.0, 21.0, 22.0, 20.5, 80.0, 85.0, 90.0, 82.0],
                      "vibration": [0.1, 0.2, 0.1, 0.15, 3.0, 3.5, 2.8, 3.2]})
    y = [0, 0, 0, 0, 1, 1, 1, 1]
    estimator = linear_model.LogisticRegression().fit(X, y)
    name = "ci-promoted-" + uuid.uuid4().hex[:8]
    with mlflow.start_run():
        mlflow_sklearn.log_model(estimator, name="model", serialization_format="skops",
                                 input_example=X.head(2), registered_model_name=name)
        if sample is not None:
            industryflow.log_feature_sample(sample)
        if raw_sample is not None:
            mlflow.log_text(raw_sample, industryflow.FEATURE_SAMPLE_ARTIFACT)
    monkeypatch.delenv("MLFLOW_TRACKING_TOKEN")
    return name, X


def _promote(session, name):
    return requests.post(f"{_ml()}/api/registered-models/{name}/versions/1/promote", headers=session["headers"],
                         timeout=120, json={"feature_config_id": session["config"], "model_type": "anomaly_detection"})


def _baseline(session, model):
    model_id = model.get("model_id") or model["id"]
    r = requests.get(f"{_ml()}/api/models/{model_id}", headers=session["headers"], timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()["baseline"]


def test_a_promoted_version_carries_the_sample_its_run_logged(session, monkeypatch):
    # The sample is logged by the run that logged the model, under the platform's artifact name.
    sampled, _ = _kernel_run(session, monkeypatch, sample=pd.DataFrame(
        {"temperature": [20.0, 21.0, 80.0], "vibration": [0.1, 0.2, 3.0]}))
    r = _promote(session, sampled)
    assert r.status_code == 201, f"promote -> {r.status_code} {r.text[:400]}"
    baseline = _baseline(session, r.json())
    assert baseline["data_drift"] is True, f"the run's sample was not read: {baseline}"


def test_a_run_that_logged_no_sample_is_promoted_without_a_baseline_and_says_so(session, monkeypatch):
    name, _ = _kernel_run(session, monkeypatch)
    r = _promote(session, name)
    assert r.status_code == 201, f"promote -> {r.status_code} {r.text[:400]}"
    baseline = _baseline(session, r.json())
    assert baseline["data_drift"] is False and "no feature sample" in baseline["reason"], baseline


def test_a_sample_that_is_not_json_is_refused_with_its_reason(session, monkeypatch):
    name, _ = _kernel_run(session, monkeypatch, raw_sample="{not json")
    r = _promote(session, name)
    assert r.status_code == 201, f"promote -> {r.status_code} {r.text[:400]}"
    baseline = _baseline(session, r.json())
    assert baseline["data_drift"] is False and "not JSON" in (baseline["reason"] or ""), baseline


def test_another_tenants_version_is_not_found(session, monkeypatch):
    other, _ = _kernel_run(dict(session, company_id=str(uuid.uuid4())), monkeypatch)
    assert _promote(session, other).status_code == 404, "a version of another tenant's namespace was promoted"
