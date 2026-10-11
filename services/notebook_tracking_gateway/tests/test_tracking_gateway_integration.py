# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
End-to-end proof for the experiment-tracking gateway (ADR-0019) against a live stack.

Every other gateway test uses a fake MLflow upstream + store; this one drives the REAL path: a
running gateway in front of a real MLflow server, with Redis (the capability store) and MinIO (the
artifact store). It proves what the fakes cannot:

  * a valid tracking handle creates + reads its OWN tenant's experiment, tenant-namespaced (the
    prefix added on the way in, stripped on the way out);
  * two tenants using the SAME experiment name get DISTINCT experiments, and neither can read the
    other's by id (cross-tenant denied through the real MLflow);
  * an artifact round-trips kernel<->object store through tenant-scoped pre-signed URLs, and
    another tenant cannot download it;
  * a wrong-audience handle and a missing bearer are refused.

IMPORTANT: run from INSIDE the compose network — the artifact 307 points at the object store's
endpoint (minio:9000), which a host runner could not follow. See tracking-gateway-integration.yml.

SKIPS when the stack is unreachable; IF_REQUIRE_LIVE_STACK (conftest.py) turns that skip into a
failure in the CI job that stands the stack up.
"""
import json
import os
import sys
import uuid

import pytest

requests = pytest.importorskip("requests")
redis = pytest.importorskip("redis")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

pytestmark = pytest.mark.integration

_KEY_PREFIX = "nbcap:"


def _gw():
    return os.getenv("TRACKING_GATEWAY_URL", "http://notebook-tracking-gateway:5050").rstrip("/")


def _redis_client():
    return redis.Redis.from_url(os.getenv("TRACKING_REDIS_URL", "redis://redis:6379/0"))


def _mint(rc, *, company_id, audience="tracking", user="e2e-tester"):
    handle = "e2e-" + uuid.uuid4().hex
    rc.set(f"{_KEY_PREFIX}{handle}", json.dumps(
        {"user": user, "company_id": company_id, "audience": audience}), ex=300)
    return handle


def _hdr(handle):
    return {"Authorization": f"Bearer {handle}"}


def _mlflow(handle, endpoint, *, method="POST", body=None, params=None):
    return requests.request(method, f"{_gw()}/api/2.0/mlflow/{endpoint}",
                            headers=_hdr(handle), json=body, params=params, timeout=30)


@pytest.fixture(scope="module")
def stack():
    try:
        rc = _redis_client(); rc.ping()
        # The gateway must be up AND able to proxy MLflow — a create is the real readiness check.
        probe = requests.post(f"{_gw()}/api/2.0/mlflow/experiments/create", timeout=10,
                              headers=_hdr("__unminted__"), json={"name": "probe"})
        # 401 (handle refused) proves the gateway is serving; anything else still means "reachable".
        _ = probe.status_code
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"tracking gateway stack not reachable: {e}")
    yield {"rc": rc, "cid_a": str(uuid.uuid4()), "cid_b": str(uuid.uuid4())}


def test_experiments_are_tenant_namespaced_and_isolated(stack):
    a = _mint(stack["rc"], company_id=stack["cid_a"])
    b = _mint(stack["rc"], company_id=stack["cid_b"])
    name = f"e2e-exp-{uuid.uuid4().hex}"

    r = _mlflow(a, "experiments/create", body={"name": name})
    assert r.status_code == 200, r.text
    exp_a = r.json()["experiment_id"]

    # A reads its own experiment back by name — unqualified (the tenant prefix is stripped).
    r = _mlflow(a, "experiments/get-by-name", method="GET", params={"experiment_name": name})
    assert r.status_code == 200 and r.json()["experiment"]["name"] == name

    # B creates the SAME name — a distinct experiment in B's namespace.
    r = _mlflow(b, "experiments/create", body={"name": name})
    assert r.status_code == 200, r.text
    exp_b = r.json()["experiment_id"]
    assert exp_a != exp_b

    # A cannot read B's experiment by id — cross-tenant denied at the gateway (ADR-0019 dec 5).
    r = _mlflow(a, "experiments/get", method="GET", params={"experiment_id": exp_b})
    assert r.status_code == 403


def test_wrong_audience_and_no_bearer_refused(stack):
    cold = _mint(stack["rc"], company_id=stack["cid_a"], audience="cold")  # wrong plane
    assert _mlflow(cold, "experiments/search", method="GET").status_code == 401
    assert requests.get(f"{_gw()}/api/2.0/mlflow/experiments/search", timeout=30).status_code == 401


def test_artifact_round_trips_via_presigned_urls(stack):
    a = _mint(stack["rc"], company_id=stack["cid_a"])
    path = f"e2e/{uuid.uuid4().hex}.bin"
    body = b"artifact-bytes-\x00\x01PAR1"
    art = f"{_gw()}/api/2.0/mlflow-artifacts/artifacts/{path}"

    # Upload: 307 -> pre-signed PUT (requests re-sends the body on the 307), bytes go direct to store.
    r = requests.put(art, data=body, headers=_hdr(a), timeout=60)
    assert r.status_code in (200, 204), r.text
    # Download: 307 -> pre-signed GET -> the same bytes.
    r = requests.get(art, headers=_hdr(a), timeout=60)
    assert r.status_code == 200 and r.content == body
    # List shows it, scoped to the tenant.
    r = requests.get(f"{_gw()}/api/2.0/mlflow-artifacts/artifacts", params={"path": "e2e"},
                     headers=_hdr(a), timeout=30)
    assert any(f["path"].endswith(".bin") for f in r.json()["files"])

    # Another tenant cannot download A's artifact: the key is re-scoped under B -> not A's bytes.
    b = _mint(stack["rc"], company_id=stack["cid_b"])
    r = requests.get(art, headers=_hdr(b), timeout=60)
    assert r.content != body


def test_cross_tenant_routes_are_refused_live(stack):
    """ADR-0019 dec 5 against the real MLflow: each of these was a cross-tenant path on a deployed stack
    before the gateway became default-deny (logged models, metric history, experiment rename, registry
    writes by qualified name, model-version sources, server-global routes)."""
    a = _mint(stack["rc"], company_id=stack["cid_a"])
    b = _mint(stack["rc"], company_id=stack["cid_b"])
    exp_a = _mlflow(a, "experiments/create", body={"name": f"e2e-x-{uuid.uuid4().hex}"}).json()["experiment_id"]
    run_a = _mlflow(a, "runs/create", body={"experiment_id": exp_a}).json()["run"]["info"]["run_id"]
    assert _mlflow(a, "runs/log-metric", body={"run_id": run_a, "key": "m", "value": 1.0,
                                               "timestamp": 0, "step": 0}).status_code == 200
    r = _mlflow(a, "logged-models", body={"experiment_id": exp_a, "name": "lm", "source_run_id": run_a})
    assert r.status_code == 200, r.text
    lm_a = r.json()["model"]["info"]["model_id"]
    model = f"e2e-rm-{uuid.uuid4().hex[:8]}"
    assert _mlflow(a, "registered-models/create", body={"name": model}).status_code == 200
    assert _mlflow(b, "registered-models/create", body={"name": model}).status_code == 200

    # The owner can do all of it.
    assert _mlflow(a, f"logged-models/{lm_a}", method="GET").status_code == 200
    assert _mlflow(a, "metrics/get-history", method="GET",
                   params={"run_id": run_a, "metric_key": "m"}).status_code == 200
    r = _mlflow(a, "model-versions/create", body={"name": model, "source": f"runs:/{run_a}/model", "run_id": run_a})
    assert r.status_code == 200, r.text

    # Another tenant can do none of it.
    refused = [
        _mlflow(b, f"logged-models/{lm_a}", method="GET"),
        _mlflow(b, f"logged-models/{lm_a}/tags", method="PATCH", body={"tags": [{"key": "x", "value": "1"}]}),
        _mlflow(b, "logged-models/search", body={"experiment_ids": [exp_a]}),
        _mlflow(b, "metrics/get-history", method="GET", params={"run_id": run_a, "metric_key": "m"}),
        _mlflow(b, "artifacts/list", method="GET", params={"run_id": run_a}),
        _mlflow(b, "experiments/update", body={"experiment_id": exp_a, "new_name": "taken"}),
        _mlflow(b, "experiments/set-experiment-tag", body={"experiment_id": exp_a, "key": "x", "value": "1"}),
        _mlflow(b, "model-versions/create",
                body={"name": model, "source": f"runs:/{run_a}/model", "run_id": run_a}),
        _mlflow(b, "model-versions/create", body={"name": model, "source": f"models:/{lm_a}", "model_id": lm_a}),
        _mlflow(b, "model-versions/create", body={"name": model, "source": "s3://mlflow/anything"}),
        _mlflow(b, "webhooks", body={"name": "x", "url": "http://127.0.0.1:9/",
                                     "events": [{"entity": "registered_model", "action": "created"}]}),
        _mlflow(b, "traces", method="GET", params={"experiment_ids": exp_a}),
    ]
    assert [r.status_code for r in refused] == [403] * len(refused), [r.text[:80] for r in refused]

    # A registry write that names A's model by its full qualified name stays in B's namespace.
    qualified_a = f"tenant_{stack['cid_a'].replace('-', '_')}.{model}"
    _mlflow(b, "registered-models/set-tag", body={"name": qualified_a, "key": "x", "value": "1"})
    tags = _mlflow(a, "registered-models/get", method="GET", params={"name": model}).json()["registered_model"]
    assert not any(t.get("key") == "x" for t in tags.get("tags", []))

    # The experiment is still A's (a rename by B would have moved it into B's namespace).
    assert _mlflow(a, "experiments/get", method="GET", params={"experiment_id": exp_a}).status_code == 200
