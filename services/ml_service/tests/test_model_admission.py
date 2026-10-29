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
import auth  # noqa: E402
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
        self.profiles = []

    async def create_model(self, company_id, model_data):
        self.created.append((company_id, model_data))
        return uuid.uuid4()

    async def get_model_by_id(self, company_id, model_id):
        return self.row

    async def get_feature_config_by_id(self, company_id, config_id):
        return {"id": config_id, "base_sensors": ["a"],
                "transformations": [{"name": "a", "type": "identity", "sensor": "a"}]} \
            if config_id == "fc1" else None

    async def sensor_ids_by_name(self, company_id, equipment_id, names):
        return {"a": "11111111-1111-1111-1111-111111111111"} if "a" in names else {}

    async def update_model_compatibility(self, **kw):
        return True

    async def update_model_reference_profile(self, company_id, model_id, profile):
        self.profiles.append(profile)
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
    # A writer: registration and deployment are role-gated (test_write_roles.py).
    app.dependency_overrides[auth.verify_jwt_token] = lambda: {"user_id": "user-1", "payload": {}, "role": "engineer"}
    app.state.ml_repository = FakeRepo()
    monkeypatch.setattr(config.config, "UPLOAD_ARTIFACT_BUCKET", BUCKET)

    async def compatible(_):
        return model_compat.Compatibility(status=model_compat.COMPATIBLE, flavor="mlflow.sklearn")

    monkeypatch.setattr(model_compat, "evaluate_uri", compatible)
    monkeypatch.setattr(model_compat, "evaluate_run", compatible)
    # The manifest read by the author-code check (its own suite: test_upload_author_code).
    monkeypatch.setattr(skops_trust, "read_flavors", lambda uri: SAFE_FLAVORS)
    # A run's model is addressed through the tracking store; no store here (ADR-0037 reads its manifest).
    monkeypatch.setattr(model_loader, "resolve_model_uri", lambda run_id: f"runs:/{run_id}/model")
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


def test_a_sample_carrying_scores_registers_the_model_without_a_baseline(client):
    """ADR-0035 dec 2, 4: a supplied score column is not accepted, and a baseline is monitoring, not
    admission — the model is registered, visibly without one."""
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine", feature_config_id="fc1",
                                              reference_profile=_profile([0.1, 0.0, 1.0, 0.4])))
    assert r.status_code == 201, r.text
    assert "derives them" in r.json()["baseline"]["reason"]
    assert "refused" in client.repo.created[0][1]["reference_profile"]


def test_a_sample_of_the_bound_inputs_is_the_baseline(client):
    sample = _profile([0.1, 0.0, 1.0, 0.4])
    sample.update(prediction_column=None)
    del sample["sample"]["prediction_score"]
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine", feature_config_id="fc1",
                                              equipment_id="22222222-2222-2222-2222-222222222222",
                                              reference_profile=sample))
    assert r.status_code == 201, r.text
    stored = client.repo.created[0][1]
    assert stored["reference_profile"]["columns"] == ["a"] and "refused" not in stored["reference_profile"]
    assert stored["sensor_ids"] == ["11111111-1111-1111-1111-111111111111"]
    assert r.json()["baseline"]["data_drift"] is True


def test_a_sample_of_other_inputs_registers_the_model_without_a_baseline(client):
    sample = {"schema_version": 2, "method": "evidently_sample", "columns": ["b"], "prediction_column": None,
              "n_training_rows": 2, "n_samples": 2, "sample": {"b": [1.0, 2.0]}}
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine", feature_config_id="fc1",
                                              reference_profile=sample))
    assert r.status_code == 201, r.text
    assert "not the feature configuration's inputs" in r.json()["baseline"]["reason"]


def test_an_unknown_feature_configuration_is_refused(client):
    r = client.post("/api/models", json=_body(mlflow_run_id="run-mine", feature_config_id="nope"))
    assert r.status_code == 422, r.text
    assert client.repo.created == []


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

    async def probe(uri, sample=None):
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
    async def probe(uri, sample=None):
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


# --- the deployment gate derives the baseline's scores (ADR-0035 dec 2, 3, 5) ----------------------

from drift import build_reference_profile, refused_baseline  # noqa: E402
from extensions import DetectionResult, UninterpretableModel  # noqa: E402
import routers.models as models_router  # noqa: E402

SAMPLE = build_reference_profile({"a": [float(v) for v in range(40)]})


def _kernel_row(**over):
    row = {"model_name": "m", "mlflow_run_id": "run-mine", "detector": "sklearn", "feature_config_id": "fc1",
           "reference_profile": SAMPLE}
    row.update(over)
    return row


def _scoring(monkeypatch, fn):
    """The detector the record names, replaced by ``fn`` (row -> score), and a loaded model stub."""
    async def detector(features, model, threshold, ctx):
        return DetectionResult(score=fn(features), is_anomaly=False)

    async def load(company_id, model):
        return object()

    monkeypatch.setattr(models_router, "detector_for", lambda model: ("sklearn", detector))
    monkeypatch.setattr(models_router, "load_model_or_refuse", load)


def test_a_kernel_models_sample_is_scored_by_its_detector_at_deployment(client, monkeypatch):
    _scoring(monkeypatch, lambda f: float(f[0, 0]) / 100)
    assert _deploy(client, _kernel_row()).status_code == 200
    stored = client.repo.profiles[-1]
    assert stored["prediction_baseline"]["origin"] == "derived"
    assert stored["sample"]["prediction_score"] == [v / 100 for v in range(40)]
    assert client.repo.status_updates == ["production"]


def test_a_sample_stored_before_the_bound_is_scored_and_stored_at_it(client, monkeypatch):
    # ADR-0035 dec 8: the gate scores at most the bound, whatever was accepted before it held.
    from drift.reference_profile import DEFAULT_MAX_SAMPLE_ROWS
    n = DEFAULT_MAX_SAMPLE_ROWS * 2
    legacy = build_reference_profile({"a": [float(v) for v in range(n)]}, max_sample_rows=n)
    scored = []
    _scoring(monkeypatch, lambda f: scored.append(1) or 0.1)
    assert _deploy(client, _kernel_row(reference_profile=legacy)).status_code == 200
    stored = client.repo.profiles[-1]
    assert len(scored) == DEFAULT_MAX_SAMPLE_ROWS == stored["n_samples"] == len(stored["sample"]["a"])
    assert stored["n_training_rows"] == n


def test_a_detector_that_cannot_score_the_sample_leaves_data_drift_only(client, monkeypatch):
    def refuse(_):
        raise UninterpretableModel("no declared semantics")

    _scoring(monkeypatch, refuse)
    assert _deploy(client, _kernel_row()).status_code == 200
    stored = client.repo.profiles[-1]
    assert stored["prediction_baseline"]["origin"] is None
    assert "no declared semantics" in stored["prediction_baseline"]["reason"]
    assert "prediction_score" not in stored["sample"]


def test_a_kernel_model_the_gate_cannot_load_is_not_deployed(client, monkeypatch):
    from fastapi import HTTPException

    async def unloadable(company_id, model):
        raise HTTPException(status_code=422, detail="not yours")

    monkeypatch.setattr(models_router, "load_model_or_refuse", unloadable)
    assert _deploy(client, _kernel_row()).status_code == 409
    assert client.repo.status_updates == [] and client.repo.profiles == []


def test_a_refused_sample_is_not_scored(client, monkeypatch):
    loads = []

    async def load(company_id, model):
        loads.append(model)

    monkeypatch.setattr(models_router, "load_model_or_refuse", load)
    assert _deploy(client, _kernel_row(reference_profile=refused_baseline("why"))).status_code == 200
    assert loads == [] and client.repo.profiles == []


def test_an_uploads_sample_is_scored_in_the_probes_own_load(client, monkeypatch):
    seen = []

    async def probe(uri, sample=None):
        seen.append(sample)
        return load_probe.ProbeResult(True, "score", scores=[0.25] * len(sample["rows"]))

    monkeypatch.setattr(load_probe, "probe", probe)
    row = _kernel_row(mlflow_run_id=None, artifact_uri=ADMITTED, provenance="uploaded")
    assert _deploy(client, row).status_code == 200
    assert seen[0]["detector"] == "sklearn" and seen[0]["rows"][3] == [3.0]
    assert client.repo.profiles[-1]["sample"]["prediction_score"] == [0.25] * 40


def test_an_upload_the_probe_refuses_records_no_baseline(client, monkeypatch):
    async def probe(uri, sample=None):
        return load_probe.ProbeResult(False, "load", "broken")

    monkeypatch.setattr(load_probe, "probe", probe)
    row = _kernel_row(mlflow_run_id=None, artifact_uri=ADMITTED, provenance="uploaded")
    assert _deploy(client, row).status_code == 409
    assert client.repo.profiles == [] and client.repo.status_updates == []


def test_any_scoring_failure_leaves_data_drift_only(client, monkeypatch):
    def wrong_width(_):
        raise ValueError("X has 1 features, but the model expects 4")

    _scoring(monkeypatch, wrong_width)
    assert _deploy(client, _kernel_row()).status_code == 200
    assert "expects 4" in client.repo.profiles[-1]["prediction_baseline"]["reason"]


def test_a_load_that_fails_outright_is_a_refusal_not_a_500(client, monkeypatch):
    async def broken(company_id, model):
        raise OSError("object store reset the connection")

    monkeypatch.setattr(models_router, "load_model_or_refuse", broken)
    r = _deploy(client, _kernel_row())
    assert r.status_code == 409 and "object store" in r.json()["detail"]["error"]


def test_a_stored_sample_that_is_not_one_is_not_loaded_for(client, monkeypatch):
    # A baseline recorded before ADR-0035 dec 4 was never held to the sample rule.
    loads = []

    async def load(company_id, model):
        loads.append(model)

    monkeypatch.setattr(models_router, "load_model_or_refuse", load)
    legacy = {**SAMPLE, "sample": {"a": [1.0, "x"]}}
    assert _deploy(client, _kernel_row(reference_profile=legacy)).status_code == 200
    assert loads == []
    assert "not a number" in client.repo.profiles[-1]["prediction_baseline"]["reason"]
