# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Gateway orchestration tests (ADR-0019): bearer auth, request/response tenant-scoping, id-ownership
refusal, and artifact pre-signing — driven through a fake MLflow upstream + store, no real MLflow.
"""
import json
import os
import sys
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gateway  # noqa: E402
import policy  # noqa: E402

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
HANDLE = "track-handle"


class FakeStore:
    def __init__(self, mapping):
        self.mapping = mapping

    async def get(self, key):
        return self.mapping.get(key)


class FakeUpstream:
    """Records calls; answers experiments/get for ownership and echoes create bodies."""

    def __init__(self, experiments):
        self.experiments = experiments  # exp_id -> qualified name
        self.calls = []

    async def call(self, method, endpoint, *, params, body):
        self.calls.append((method, endpoint, params, body))
        if endpoint == "experiments/get":
            name = self.experiments.get(str(params.get("experiment_id")))
            return (200, {"experiment": {"experiment_id": params["experiment_id"], "name": name}}) if name else (404, {})
        if endpoint == "experiments/create":
            return 200, {"experiment_id": "99"}
        if endpoint == "experiments/get-by-name":
            return 200, {"experiment": {"experiment_id": "1", "name": params.get("experiment_name")}}
        if endpoint == "runs/create":
            return 200, {"run": {"info": {"run_id": "r1", "experiment_id": body.get("experiment_id")}}}
        return 200, {}


class FakeSigner:
    def presign(self, method, key):
        return f"https://store.local/{key}?sig=fake&m={method}"


def _client(experiments=None):
    store = FakeStore({f"{policy._KEY_PREFIX}{HANDLE}": json.dumps(
        {"user": "ds", "company_id": CID, "audience": "tracking", "read_only": False})})
    up = FakeUpstream(experiments or {})
    app = gateway.build_app(store.get, up, FakeSigner())
    c = TestClient(app)
    c.up = up
    return c


def _auth(h=HANDLE):
    return {"Authorization": f"Bearer {h}"}


def test_missing_or_bad_bearer_is_401():
    c = _client()
    assert c.post("/api/2.0/mlflow/experiments/create", json={"name": "x"}).status_code == 401
    assert c.post("/api/2.0/mlflow/experiments/create", json={"name": "x"},
                  headers=_auth("nope")).status_code == 401


def test_experiment_create_name_is_tenant_qualified():
    c = _client()
    r = c.post("/api/2.0/mlflow/experiments/create", json={"name": "churn"}, headers=_auth())
    assert r.status_code == 200
    # The body proxied to MLflow carried the tenant-qualified name.
    _, endpoint, _, body = c.up.calls[-1]
    assert endpoint == "experiments/create"
    assert body["name"] == policy.tenant_prefix(CID) + "churn"


def test_get_by_name_response_is_stripped():
    c = _client()
    # MLflow get-by-name is a GET with a query param; the gateway qualifies it, MLflow echoes the
    # qualified name, and the gateway strips it back to the plain name on the way out.
    r = c.get("/api/2.0/mlflow/experiments/get-by-name?experiment_name=churn", headers=_auth())
    assert r.status_code == 200
    assert r.json()["experiment"]["name"] == "churn"
    # And MLflow saw the tenant-qualified name.
    _, endpoint, params, _ = c.up.calls[-1]
    assert params["experiment_name"] == policy.tenant_prefix(CID) + "churn"


def test_run_create_in_own_experiment_ok():
    c = _client(experiments={"5": policy.tenant_prefix(CID) + "churn"})
    r = c.post("/api/2.0/mlflow/runs/create", json={"experiment_id": "5"}, headers=_auth())
    assert r.status_code == 200


def test_run_create_in_foreign_experiment_refused():
    c = _client(experiments={"7": policy.tenant_prefix(OTHER) + "secret"})
    r = c.post("/api/2.0/mlflow/runs/create", json={"experiment_id": "7"}, headers=_auth())
    assert r.status_code == 403


def test_artifact_returns_tenant_scoped_presigned_url():
    c = _client()
    r = c.get("/api/2.0/mlflow/artifacts?path=models/m.pkl", headers=_auth())
    body = r.json()
    assert body["key"] == policy.tenant_prefix(CID) + "models/m.pkl"
    assert body["method"] == "GET"
    assert body["url"].startswith("https://store.local/" + policy.tenant_prefix(CID))
