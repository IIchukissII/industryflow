# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Default-deny routing and per-route tenant ownership (ADR-0019 dec 5).

Every case below was a live cross-tenant path on a deployed stack before the route table existed:
the gateway forwarded any MLflow route and checked ownership only on a short list.
"""
import json
import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gateway  # noqa: E402
import policy  # noqa: E402
import routes  # noqa: E402

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
HANDLE = "track-handle"
OWN_EXP, FOREIGN_EXP = "5", "7"
OWN_RUN, FOREIGN_RUN = "a" * 32, "b" * 32
OWN_LM, FOREIGN_LM = "m-" + "1" * 32, "m-" + "2" * 32


class Upstream:
    def __init__(self):
        self.calls = []
        self.exps = {OWN_EXP: policy.tenant_prefix(CID) + "mine", FOREIGN_EXP: policy.tenant_prefix(OTHER) + "theirs"}
        self.runs = {OWN_RUN: OWN_EXP, FOREIGN_RUN: FOREIGN_EXP}
        self.models = {OWN_LM: OWN_EXP, FOREIGN_LM: FOREIGN_EXP}

    async def call(self, method, endpoint, *, params, body):
        self.calls.append((method, endpoint, params, body))
        if endpoint == "experiments/get":
            name = self.exps.get(str(params.get("experiment_id")))
            return (200, {"experiment": {"name": name}}) if name else (404, {})
        if endpoint == "runs/get":
            exp = self.runs.get(params.get("run_id") or params.get("run_uuid"))
            return (200, {"run": {"info": {"experiment_id": exp}}}) if exp else (404, {})
        if endpoint.startswith("logged-models/") and method == "GET" and endpoint.count("/") == 1:
            exp = self.models.get(endpoint.split("/", 1)[1])
            return (200, {"model": {"info": {"experiment_id": exp}}}) if exp else (404, {})
        return 200, {}


class Store:
    async def get(self, key):
        if key == f"{policy._KEY_PREFIX}{HANDLE}":
            return json.dumps({"user": "ds", "company_id": CID, "audience": "tracking"})
        return None


class Art:
    def presign(self, method, key): return f"https://store/{key}"
    def list_files(self, prefix): return []
    def delete(self, key): pass


@pytest.fixture
def c():
    up = Upstream()
    client = TestClient(gateway.build_app(Store().get, up, Art()))
    client.up = up
    return client


H = {"Authorization": f"Bearer {HANDLE}"}
API = "/api/2.0/mlflow/"


def _forwarded(c, endpoint):
    return [call for call in c.up.calls if call[1] == endpoint]


# --- default deny -------------------------------------------------------------------------------

@pytest.mark.parametrize("method,endpoint", [
    ("POST", "webhooks"), ("GET", "webhooks"), ("GET", "traces"), ("POST", "traces"),
    ("GET", "unified-traces"), ("POST", "artifacts/presigned-upload-url"),
    ("POST", "artifacts/presigned-download-url"), ("GET", "get-online-trace-details"),
    ("POST", "some/future-route"), ("DELETE", "runs/get"),
])
def test_routes_without_a_rule_are_refused_and_never_forwarded(c, method, endpoint):
    r = c.request(method, API + endpoint, headers=H, json={})
    assert r.status_code == 403
    assert not _forwarded(c, endpoint)


def test_every_route_carries_at_least_one_rule():
    assert all(route.rules for route in routes.ROUTES.values())


def test_a_names_rule_without_name_fields_fails_at_import(monkeypatch):
    monkeypatch.setitem(routes.ROUTES, ("POST", "registered-models/unknown"),
                        routes.Route("registered-models/unknown", ("names",)))
    with pytest.raises(RuntimeError):
        routes._check_table()


# --- logged models --------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("GET", "logged-models/{}", None),
    ("PATCH", "logged-models/{}", {"tags": []}),
    ("DELETE", "logged-models/{}", None),
    ("PATCH", "logged-models/{}/tags", {"tags": [{"key": "k", "value": "v"}]}),
    ("DELETE", "logged-models/{}/tags/k", None),
    ("POST", "logged-models/{}/params", {"params": []}),
    ("GET", "logged-models/{}/artifacts/directories", None),
])
def test_logged_model_routes_are_owner_only(c, method, path, body):
    assert c.request(method, API + path.format(OWN_LM), headers=H, json=body).status_code == 200
    assert c.request(method, API + path.format(FOREIGN_LM), headers=H, json=body).status_code == 403


def test_logged_model_search_needs_an_owned_experiment(c):
    ok = c.post(API + "logged-models/search", headers=H, json={"experiment_ids": [OWN_EXP]})
    assert ok.status_code == 200
    assert c.post(API + "logged-models/search", headers=H, json={"experiment_ids": [FOREIGN_EXP]}).status_code == 403
    assert c.post(API + "logged-models/search", headers=H, json={}).status_code == 403


def test_logged_model_create_checks_experiment_and_source_run(c):
    assert c.post(API + "logged-models", headers=H, json={"experiment_id": OWN_EXP}).status_code == 200
    assert c.post(API + "logged-models", headers=H,
                  json={"experiment_id": OWN_EXP, "source_run_id": FOREIGN_RUN}).status_code == 403


# --- runs, metrics ---------------------------------------------------------------------------------

@pytest.mark.parametrize("method,endpoint,key", [
    ("GET", "metrics/get-history", "run_id"),
    ("GET", "artifacts/list", "run_id"),
    ("POST", "runs/restore", "run_id"),
    ("POST", "runs/log-inputs", "run_id"),
    ("POST", "runs/log-model", "run_id"),
    ("GET", "runs/get", "run_uuid"),   # the legacy alias must not bypass the check
])
def test_run_routes_are_owner_only(c, method, endpoint, key):
    send = (lambda rid: c.request(method, API + endpoint, headers=H, params={key: rid})) if method == "GET" \
        else (lambda rid: c.request(method, API + endpoint, headers=H, json={key: rid}))
    assert send(OWN_RUN).status_code == 200
    assert send(FOREIGN_RUN).status_code == 403


def test_run_route_without_a_run_id_is_refused(c):
    assert c.get(API + "metrics/get-history", headers=H, params={"metric_key": "m"}).status_code == 403


def test_bulk_metric_history_checks_every_run(c):
    r = c.get(API + "metrics/get-history-bulk-interval", headers=H,
              params=[("run_ids", OWN_RUN), ("run_ids", FOREIGN_RUN)])
    assert r.status_code == 403


# --- experiments ----------------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint,body", [
    ("experiments/update", {"new_name": "x"}),
    ("experiments/set-experiment-tag", {"key": "k", "value": "v"}),
    ("experiments/delete-experiment-tag", {"key": "k"}),
    ("experiments/restore", {}),
])
def test_experiment_routes_are_owner_only(c, endpoint, body):
    assert c.post(API + endpoint, headers=H, json={"experiment_id": OWN_EXP, **body}).status_code == 200
    assert c.post(API + endpoint, headers=H, json={"experiment_id": FOREIGN_EXP, **body}).status_code == 403


def test_experiment_rename_lands_in_the_callers_namespace(c):
    c.post(API + "experiments/update", headers=H, json={"experiment_id": OWN_EXP, "new_name": "renamed"})
    (_, _, _, body), = _forwarded(c, "experiments/update")
    assert body["new_name"] == policy.tenant_prefix(CID) + "renamed"


# --- registry -------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,endpoint", [
    ("PATCH", "model-versions/update"), ("DELETE", "model-versions/delete"),
    ("POST", "model-versions/set-tag"), ("POST", "model-versions/transition-stage"),
    ("POST", "registered-models/set-tag"), ("POST", "registered-models/rename"),
    ("DELETE", "registered-models/alias"),
])
def test_registry_writes_cannot_name_another_tenants_model(c, method, endpoint):
    foreign = policy.tenant_prefix(OTHER) + "theirs"
    c.request(method, API + endpoint, headers=H, json={"name": foreign, "version": "1"})
    (_, _, _, body), = _forwarded(c, endpoint)
    # Qualifying keeps the call in the caller's namespace: the foreign model is never the target.
    assert body["name"].startswith(policy.tenant_prefix(CID))


def test_latest_versions_lookup_is_qualified(c):
    c.get(API + "registered-models/get-latest-versions", headers=H, params={"name": policy.tenant_prefix(OTHER) + "x"})
    (_, _, params, _), = _forwarded(c, "registered-models/get-latest-versions")
    assert params["name"].startswith(policy.tenant_prefix(CID))


# --- model-version sources ------------------------------------------------------------------------

@pytest.mark.parametrize("body,status", [
    ({"source": f"runs:/{OWN_RUN}/model", "run_id": OWN_RUN}, 200),
    ({"source": f"models:/{OWN_LM}", "model_id": OWN_LM}, 200),
    ({"source": f"mlflow-artifacts:/{policy.artifact_prefix(CID)}9/models/m-x/artifacts"}, 200),
    ({"source": f"runs:/{FOREIGN_RUN}/model", "run_id": FOREIGN_RUN}, 403),
    ({"source": f"models:/{FOREIGN_LM}", "model_id": FOREIGN_LM}, 403),
    ({"source": f"runs:/{OWN_RUN}/model", "run_id": FOREIGN_RUN}, 403),
    ({"source": f"models:/{OWN_LM}", "model_id": FOREIGN_LM}, 403),
    ({"source": f"mlflow-artifacts:/{policy.artifact_prefix(OTHER)}9/models/m-x/artifacts"}, 403),
    ({"source": "s3://mlflow/tenant_x/1/models/m-1/artifacts"}, 403),
    ({"source": "/home/mlflow/anything"}, 403),
    ({"source": "models:/somemodel/1"}, 403),
    ({}, 403),
])
def test_model_version_source_must_point_into_the_callers_tenant(c, body, status):
    r = c.post(API + "model-versions/create", headers=H, json={"name": "m", **body})
    assert r.status_code == status
    assert bool(_forwarded(c, "model-versions/create")) == (status == 200)
