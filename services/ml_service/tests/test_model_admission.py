# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Registration and deployment refuse an artifact address the caller's tenant may not use.

The run id and the artifact location are both caller-supplied, and everything downstream trusts them:
the compatibility gate reads the artifact, the load probe executes its loader, and serving loads it
with a credential that reaches every tenant's artifacts. Two holes were open:

  A. an uploaded model registered with a STAGING location — bytes the gateway never admitted — went
     past the gateway's refusal entirely, and MLflow's loader would run whatever the manifest named;
  B. a model registered with another tenant's run id, or another tenant's artifact prefix, was
     loaded and served as the caller's own.

Each refusal is driven at the wire, and the pure rule underneath is pinned on its own.
"""
import os
import sys
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import config  # noqa: E402
import extensions.builtins_detectors  # noqa: F401,E402  (registers the built-ins)
import load_probe  # noqa: E402
import model_compat  # noqa: E402
import model_loader  # noqa: E402
import skops_trust  # noqa: E402
import uploaded_model  # noqa: E402
from routers.models import router, get_company_id_dependency  # noqa: E402

SAFE_FLAVORS = {"python_function": {"loader_module": "mlflow.sklearn"},
                "sklearn": {"serialization_format": "skops"}}

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
BUCKET = "mlflow"
UPLOAD_ID = "Abcdefghijklmnop_-123456"


def _token(cid):
    return "tenant_" + cid.replace("-", "_")


ADMITTED = f"s3://{BUCKET}/{_token(CID)}/uploads/{UPLOAD_ID}"


# --- the pure rule ------------------------------------------------------------------------------

def test_an_admitted_upload_of_ones_own_tenant_is_accepted():
    assert uploaded_model.admitted_artifact_refusal(ADMITTED, CID, BUCKET) is None
    assert uploaded_model.admitted_artifact_refusal(ADMITTED + "/", CID, BUCKET) is None


@pytest.mark.parametrize("uri", [
    # scenario A: bytes that were staged and never admitted
    f"s3://{BUCKET}/_upload-staging/{_token(CID)}/{UPLOAD_ID}",
    # scenario B: another tenant's admitted upload
    f"s3://{BUCKET}/{_token(OTHER)}/uploads/{UPLOAD_ID}",
    # the tenant's own prefix, but not an admitted upload
    f"s3://{BUCKET}/{_token(CID)}/1/models/m-abc/artifacts",
    f"s3://{BUCKET}/{_token(CID)}/uploads/",
    f"s3://{BUCKET}/{_token(CID)}/uploads/short",
    # traversal and smuggling out of the upload directory
    f"s3://{BUCKET}/{_token(CID)}/uploads/{UPLOAD_ID}/../../{_token(OTHER)}/uploads/{UPLOAD_ID}",
    f"s3://{BUCKET}/{_token(CID)}/uploads/{UPLOAD_ID}/sub",
    f"s3://{BUCKET}/{_token(CID)}/uploads/%2e%2e{UPLOAD_ID}",
    # another bucket, and every other scheme
    f"s3://elsewhere/{_token(CID)}/uploads/{UPLOAD_ID}",
    f"S3://{BUCKET}/{_token(CID)}/uploads/{UPLOAD_ID}",
    f"s3a://{BUCKET}/{_token(CID)}/uploads/{UPLOAD_ID}",
    f"file:///srv/{_token(CID)}/uploads/{UPLOAD_ID}",
    f"http://minio:9000/{BUCKET}/{_token(CID)}/uploads/{UPLOAD_ID}",
    f"mlflow-artifacts:/{_token(CID)}/uploads/{UPLOAD_ID}",
    "runs:/0123456789abcdef/model",
    "models:/m-0123456789abcdef",
    "/app/models/whatever",
    "",
])
def test_every_other_address_is_refused(uri):
    assert uploaded_model.admitted_artifact_refusal(uri, CID, BUCKET) is not None


def test_without_a_configured_bucket_nothing_is_an_admitted_upload():
    assert uploaded_model.admitted_artifact_refusal(ADMITTED, CID, "") is not None


def test_the_admitted_root_is_the_gateways():
    """The gateway decides where an admitted artifact lands; this service checks for it. They live in
    different images, so the value is written twice — held together here rather than by a comment."""
    src = os.path.join(os.path.dirname(__file__), "..", "..", "notebook_tracking_gateway", "staging.py")
    if not os.path.exists(src):
        pytest.skip("the gateway is not present in this checkout")
    with open(src) as fh:
        body = fh.read()
    assert f'ADMITTED_ROOT = "{uploaded_model.ADMITTED_ROOT}"' in body


# --- at the wire: registration ------------------------------------------------------------------

class FakeRepo:
    def __init__(self, row=None):
        self.created = []
        self.row = row
        self.status_updates = []

    async def create_model(self, company_id, model_data):
        self.created.append((company_id, model_data))
        return uuid.uuid4()

    async def get_model_by_id(self, company_id, model_id):
        return self.row

    async def update_model_compatibility(self, **kw):
        return True

    async def update_model_status(self, company_id, model_id, status):
        self.status_updates.append(status)
        return True


@pytest.fixture
def owned_runs(monkeypatch):
    """Which runs each tenant owns — the tracking store's answer, stubbed at the one function that
    asks it."""
    owned = {CID: {"run-mine"}, OTHER: {"run-theirs"}}
    monkeypatch.setattr(model_loader, "_run_owned_by",
                        lambda run_id, company_id: run_id in owned.get(company_id, set()))
    return owned


@pytest.fixture
def client(monkeypatch, owned_runs):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_company_id_dependency] = lambda: CID
    app.state.ml_repository = FakeRepo()
    monkeypatch.setattr(config.config, "UPLOAD_ARTIFACT_BUCKET", BUCKET)

    async def compatible(_):
        return model_compat.Compatibility(status=model_compat.COMPATIBLE, flavor="mlflow.sklearn")

    monkeypatch.setattr(model_compat, "evaluate_uri", compatible)
    monkeypatch.setattr(model_compat, "evaluate_run", compatible)
    # The manifest read by the author-code check (its own suite: test_upload_author_code).
    monkeypatch.setattr(skops_trust, "read_flavors", lambda uri: SAFE_FLAVORS)
    c = TestClient(app)
    c.repo = app.state.ml_repository
    return c


def _body(**over):
    body = {"model_name": "m", "model_version": "1", "model_type": "outlier"}
    body.update(over)
    return body


def test_scenario_a_a_staged_upload_cannot_be_registered(client):
    staged = f"s3://{BUCKET}/_upload-staging/{_token(CID)}/{UPLOAD_ID}"
    r = client.post("/api/models", json=_body(artifact_uri=staged, score_semantics="outlier_score"))
    assert r.status_code == 422, r.text
    assert client.repo.created == []


def test_scenario_b_another_tenants_upload_cannot_be_registered(client):
    theirs = f"s3://{BUCKET}/{_token(OTHER)}/uploads/{UPLOAD_ID}"
    r = client.post("/api/models", json=_body(artifact_uri=theirs, score_semantics="outlier_score"))
    assert r.status_code == 422, r.text
    assert client.repo.created == []


def test_scenario_b_another_tenants_run_cannot_be_registered(client):
    r = client.post("/api/models", json=_body(mlflow_run_id="run-theirs"))
    assert r.status_code == 422, r.text
    assert client.repo.created == []


def test_a_run_that_does_not_exist_is_refused_the_same_way(client):
    """One answer for "not yours" and "no such run", so the refusal cannot be used to probe for
    another tenant's runs."""
    theirs = client.post("/api/models", json=_body(mlflow_run_id="run-theirs")).json()
    missing = client.post("/api/models", json=_body(mlflow_run_id="run-nobody")).json()
    assert theirs["detail"]["reason"] == missing["detail"]["reason"]


def test_a_run_id_that_could_alter_a_search_filter_is_refused(client):
    r = client.post("/api/models", json=_body(mlflow_run_id="x' OR name LIKE '%"))
    assert r.status_code == 422, r.text


def test_a_run_and_an_artifact_together_are_refused(client):
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine", artifact_uri=ADMITTED))
    assert r.status_code == 422, r.text
    assert client.repo.created == []


def test_ones_own_run_and_ones_own_upload_are_registered(client):
    assert client.post("/api/models", json=_body(mlflow_run_id="run-mine")).status_code == 201
    r = client.post("/api/models", json=_body(artifact_uri=ADMITTED, score_semantics="outlier_score",
                                              model_version="2"))
    assert r.status_code == 201, r.text


def _profile(scores):
    return {"schema_version": 2, "method": "evidently_sample", "columns": ["a"],
            "prediction_column": "prediction_score", "n_training_rows": len(scores),
            "n_samples": len(scores), "sample": {"a": [1.0] * len(scores), "prediction_score": scores}}


def test_a_reference_whose_predictions_are_not_detector_scores_is_refused(client):
    """ADR-0021 dec 4: the prediction column is a baseline only on the detector's scale. A
    column of labels is refused at registration, before anything is stored."""
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine",
                                              reference_profile=_profile([1, -1, 1, 1])))
    assert r.status_code == 422, r.text
    assert "declared scale" in r.json()["detail"]["message"]
    assert client.repo.created == []


def test_a_reference_of_detector_scores_is_registered(client):
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine",
                                              reference_profile=_profile([0.1, 0.0, 1.0, 0.4])))
    assert r.status_code == 201, r.text
    assert client.repo.created[0][1]["reference_profile"]["prediction_column"] == "prediction_score"


def test_a_tracking_store_that_does_not_answer_is_transient_not_a_verdict(client, monkeypatch):
    def down(run_id, company_id):
        raise model_loader.TrackingStoreUnavailable("connection refused")
    monkeypatch.setattr(model_loader, "_run_owned_by", down)
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine"))
    assert r.status_code == 503
    assert client.repo.created == []


def test_the_address_is_judged_before_the_artifact_is_read(client, monkeypatch):
    """The compatibility gate reads the artifact. It must never be pointed at one the tenant may
    not use — refusing afterwards would already have fetched it."""
    read = []

    async def reads(uri):
        read.append(uri)
        return model_compat.Compatibility(status=model_compat.COMPATIBLE, flavor="mlflow.sklearn")

    monkeypatch.setattr(model_compat, "evaluate_uri", reads)
    staged = f"s3://{BUCKET}/_upload-staging/{_token(CID)}/{UPLOAD_ID}"
    client.post("/api/models", json=_body(artifact_uri=staged, score_semantics="outlier_score"))
    assert read == []


# --- at the wire: deployment (rows registered before the check existed) ------------------------

def _deploy(client, row):
    client.repo.row = row
    return client.post("/api/models/m1/deploy", json={"model_id": "m1", "environment": "production"})


def test_a_row_carrying_a_staged_artifact_is_not_deployed_or_probed(client, monkeypatch):
    probed = []

    async def probe(uri):
        probed.append(uri)
        return load_probe.ProbeResult(True, "score")

    monkeypatch.setattr(load_probe, "probe", probe)
    row = {"model_name": "m", "artifact_uri": f"s3://{BUCKET}/_upload-staging/{_token(CID)}/{UPLOAD_ID}",
           "provenance": "uploaded", "detector": "outlier"}
    r = _deploy(client, row)
    assert r.status_code == 409, r.text
    assert probed == [], "the probe executed the loader of bytes the gateway never admitted"
    assert client.repo.status_updates == []


def test_a_row_carrying_another_tenants_run_is_not_deployed(client):
    r = _deploy(client, {"model_name": "m", "mlflow_run_id": "run-theirs", "detector": "sklearn"})
    assert r.status_code == 409, r.text
    assert client.repo.status_updates == []


def test_a_row_of_ones_own_is_deployed(client, monkeypatch):
    async def probe(uri):
        return load_probe.ProbeResult(True, "score")

    monkeypatch.setattr(load_probe, "probe", probe)
    assert _deploy(client, {"model_name": "m", "mlflow_run_id": "run-mine", "detector": "sklearn"}).status_code == 200
    assert _deploy(client, {"model_name": "m", "artifact_uri": ADMITTED, "provenance": "uploaded",
                            "detector": "outlier"}).status_code == 200


def test_undeploying_is_never_blocked_by_the_address(client):
    """Deprecating a model is the remedy for a bad one — including one registered with an address it
    should never have had."""
    client.repo.row = {"model_name": "m", "mlflow_run_id": "run-theirs"}
    r = client.post("/api/models/m1/deploy", json={"model_id": "m1", "environment": "deprecated"})
    assert r.status_code == 200, r.text
