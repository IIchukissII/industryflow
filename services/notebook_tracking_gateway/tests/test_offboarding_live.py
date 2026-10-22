# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A deleted tenant leaves the tracking store, against a live gateway, MLflow and MinIO (ADR-0033 dec 4).

Two phases, with the tracking server's collection run between them as its own container
(tracking-gateway-integration.yml): ``-k emptied`` seeds two tenants and empties one through the
operator plane; ``-k collected`` checks, after the collection, that nothing of it remains and that
the other tenant, its own trash included, is untouched. The phases share a state file
(OFFBOARDING_STATE). Not named ``*_integration.py``, so the gateway's other proofs never run it out
of order.

Needs TRACKING_OPERATOR_TOKEN, the secret the gateway was started with. Run inside the compose network.
"""
import json
import os
import uuid

import pytest

requests = pytest.importorskip("requests")
redis = pytest.importorskip("redis")

pytestmark = pytest.mark.integration

GW = os.getenv("TRACKING_GATEWAY_URL", "http://notebook-tracking-gateway:5050").rstrip("/")
MLFLOW = os.getenv("MLFLOW_DIRECT_URL", "http://mlflow:5000").rstrip("/")
STATE = os.getenv("OFFBOARDING_STATE", "/tmp/offboarding-state.json")


def _token():
    token = os.getenv("TRACKING_OPERATOR_TOKEN")
    if not token:
        pytest.skip("TRACKING_OPERATOR_TOKEN unset")
    return token


def _handle(company_id):
    handle = "e2e-" + uuid.uuid4().hex
    redis.Redis.from_url(os.getenv("TRACKING_REDIS_URL", "redis://redis:6379/0")).set(
        f"nbcap:{handle}", json.dumps({"user": "e2e", "company_id": company_id, "audience": "tracking"}), ex=600)
    return {"Authorization": f"Bearer {handle}"}


def _ok(r):
    assert r.status_code == 200, f"{r.request.method} {r.url}: {r.status_code} {r.text}"
    return r.json() if r.content else {}


def _seed(company_id):
    """What a kernel leaves: a live experiment with a run, its artifact and a logged model, a
    registered model, a deleted run and a deleted experiment."""
    h = _handle(company_id)
    api = f"{GW}/api/2.0/mlflow/"
    exp = _ok(requests.post(api + "experiments/create", headers=h, json={"name": "exp"}, timeout=30))["experiment_id"]
    run = _ok(requests.post(api + "runs/create", headers=h, json={"experiment_id": exp}, timeout=30))["run"]["info"]
    # The run's artifact, where MLflow records it (mlflow-artifacts:/<tenant>/<nonce>/<run>/artifacts).
    rel = run["artifact_uri"].split(":/", 1)[1].split("/", 1)[1]
    r = requests.put(f"{GW}/api/2.0/mlflow-artifacts/artifacts/{rel}/a.bin", data=b"bytes", headers=h, timeout=60)
    assert r.status_code in (200, 204), r.text
    _ok(requests.post(api + "logged-models", headers=h, json={"experiment_id": exp, "name": "lm"}, timeout=30))
    _ok(requests.post(api + "registered-models/create", headers=h, json={"name": "model"}, timeout=30))
    trashed_run = _ok(requests.post(api + "runs/create", headers=h, json={"experiment_id": exp}, timeout=30))["run"]["info"]["run_id"]
    _ok(requests.post(api + "runs/delete", headers=h, json={"run_id": trashed_run}, timeout=30))
    trashed_exp = _ok(requests.post(api + "experiments/create", headers=h, json={"name": "trashed"}, timeout=30))["experiment_id"]
    _ok(requests.post(api + "experiments/delete", headers=h, json={"experiment_id": trashed_exp}, timeout=30))
    return {"exp": exp, "run": run["run_id"], "artifact": f"{rel}/a.bin", "trashed_run": trashed_run,
            "trashed_exp": trashed_exp}


def _empty(company_id, token):
    return requests.delete(f"{GW}/api/2.0/industryflow-operator/tenants/{company_id}",
                           headers={"Authorization": f"Bearer {token}"}, timeout=300)


def _direct(endpoint, **params):
    return requests.get(f"{MLFLOW}/api/2.0/mlflow/{endpoint}", params=params, timeout=30)


def test_emptied_through_the_operator_plane():
    token = _token()
    gone, kept = str(uuid.uuid4()), str(uuid.uuid4())
    seeded = {"gone": gone, "kept": kept, "g": _seed(gone), "k": _seed(kept)}

    assert _empty(gone, "wrong").status_code == 401
    assert _empty(gone, _handle(gone)["Authorization"][7:]).status_code == 401

    answer = _ok(_empty(gone, token))
    # The two experiments wait for the collection; models and artifacts are gone now.
    assert answer == {"company_id": gone, "remaining": {"experiments": 2, "registered_models": 0, "artifacts": 0}}
    assert _ok(_empty(gone, token)) == answer
    for key in ("exp", "trashed_exp"):
        experiment = _ok(_direct("experiments/get", experiment_id=seeded["g"][key]))["experiment"]
        assert experiment["lifecycle_stage"] == "deleted" and experiment["name"].startswith("_deleted.tenant_")
    with open(STATE, "w") as f:
        json.dump(seeded, f)


def test_collected_and_nothing_else_touched():
    token = _token()
    with open(STATE) as f:
        seeded = json.load(f)

    assert _ok(_empty(seeded["gone"], token))["remaining"] == {"experiments": 0, "registered_models": 0, "artifacts": 0}
    for key in ("exp", "trashed_exp"):
        assert _direct("experiments/get", experiment_id=seeded["g"][key]).status_code == 404
    for key in ("run", "trashed_run"):
        assert _direct("runs/get", run_id=seeded["g"][key]).status_code == 404

    kept, h = seeded["k"], _handle(seeded["kept"])
    assert _ok(_direct("experiments/get", experiment_id=kept["exp"]))["experiment"]["lifecycle_stage"] == "active"
    # The kept tenant's own trash is still restorable.
    assert _ok(_direct("experiments/get", experiment_id=kept["trashed_exp"]))["experiment"]["lifecycle_stage"] == "deleted"
    assert _ok(_direct("runs/get", run_id=kept["trashed_run"]))["run"]["info"]["lifecycle_stage"] == "deleted"
    r = requests.get(f"{GW}/api/2.0/mlflow-artifacts/artifacts/{kept['artifact']}", headers=h, timeout=60)
    assert r.status_code == 200 and r.content == b"bytes"
    models = _ok(requests.get(f"{GW}/api/2.0/mlflow/registered-models/search", headers=h, timeout=30))
    assert [m["name"] for m in models.get("registered_models", [])] == ["model"]
