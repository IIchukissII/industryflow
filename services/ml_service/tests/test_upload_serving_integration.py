# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
End-to-end proof for the serving half of the upload path (ADR-0030) against a live stack.

The gateway's live proof stops at commit: it never loads a model, so its artifacts are synthetic.
Everything after commit is this service's: the registration gate (dec 4-6) and the deployment gate's
load-and-score probe (dec 7). Their unit tests fake MLflow, the object store and the probe's
subprocess, so a serving image that cannot load what the gateway admitted would leave them green.

This drives the whole path a client does, with no shortcut through the stores: log in at the API
gateway, mint the upload capability at this service, stage/PUT/commit at the tracking gateway (at the
address this service advertises), then register and deploy here. The models are real: saved by
MLflow in the non-executing skops format, as an external author would, with the serving image's own
library versions.

IMPORTANT: run from INSIDE the compose network — the pre-signed URLs point at minio:9000. SKIPS when
the stack is unreachable; IF_REQUIRE_LIVE_STACK (conftest.py) turns that skip into a failure in CI.
"""
import os
import uuid

import pytest

requests = pytest.importorskip("requests")
pd = pytest.importorskip("pandas")
pytest.importorskip("skops")
mlflow_sklearn = pytest.importorskip("mlflow.sklearn")
models = pytest.importorskip("mlflow.models")
ensemble = pytest.importorskip("sklearn.ensemble")
linear_model = pytest.importorskip("sklearn.linear_model")

pytestmark = pytest.mark.integration

FEATURES = ["temperature", "vibration"]
UPLOAD = "/api/2.0/industryflow-upload"


def _ml():
    return os.getenv("ML_SERVICE_URL", "http://ml-service-api:8002").rstrip("/")


def _api():
    return os.getenv("API_GATEWAY_URL", "http://api-gateway:8000").rstrip("/")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def session():
    email, password = os.getenv("IF_UPLOADER_EMAIL"), os.getenv("IF_UPLOADER_PASSWORD")
    company_id = os.getenv("IF_UPLOADER_COMPANY_ID")
    if not (email and password and company_id):
        pytest.skip("IF_UPLOADER_EMAIL / IF_UPLOADER_PASSWORD / IF_UPLOADER_COMPANY_ID not set")
    try:
        requests.get(f"{_ml()}/health", timeout=5).raise_for_status()
        requests.get(f"{_api()}/health", timeout=5).raise_for_status()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"live stack unreachable: {exc}")
    r = requests.post(f"{_api()}/auth/jwt/login", timeout=30,
                      data={"username": email, "password": password})
    assert r.status_code == 200, f"login -> {r.status_code} {r.text[:200]}"
    return {"headers": _bearer(r.json()["access_token"]), "company_id": company_id}


def _training_data():
    X = pd.DataFrame({
        "temperature": [20.0, 21.0, 22.0, 20.5, 80.0, 85.0, 90.0, 82.0],
        "vibration": [0.1, 0.2, 0.1, 0.15, 3.0, 3.5, 2.8, 3.2],
    })
    y = [0, 0, 0, 0, 1, 1, 1, 1]
    return X, y


def _save(estimator, path, *, trusted_types=None):
    X, y = _training_data()
    estimator.fit(X, y)
    mlflow_sklearn.save_model(
        estimator, str(path), serialization_format="skops", skops_trusted_types=trusted_types,
        signature=models.infer_signature(X, estimator.predict(X)),
    )
    return path


def _upload(session, model_dir):
    """Mint, stage, PUT, commit: the client's half, through each service's real surface."""
    r = requests.post(f"{_ml()}/api/models/upload-capability", headers=session["headers"], timeout=30)
    assert r.status_code == 201, f"mint -> {r.status_code} {r.text[:200]}"
    capability, gateway = r.json()["capability"], r.json()["upload_url"]
    assert gateway, "this service advertises no upload endpoint"

    files = sorted(p.name for p in model_dir.iterdir() if p.is_file())
    r = requests.post(f"{gateway}{UPLOAD}/stage", headers=_bearer(capability), timeout=30,
                      json={"files": files})
    assert r.status_code == 200, f"stage -> {r.status_code} {r.text[:200]}"
    staged = r.json()
    for member, url in staged["urls"].items():
        put = requests.put(url, data=(model_dir / member).read_bytes(), timeout=60)
        assert put.status_code in (200, 204), f"PUT {member} -> {put.status_code} {put.text[:120]}"

    r = requests.post(f"{gateway}{UPLOAD}/commit", headers=_bearer(capability), timeout=60,
                      json={"upload_id": staged["upload_id"]})
    assert r.status_code == 200, f"commit -> {r.status_code} {r.text[:200]}"
    return r.json()["artifact_uri"]


def _register(session, artifact_uri, *, semantics="anomaly_probability"):
    return requests.post(f"{_ml()}/api/models", headers=session["headers"], timeout=120, json={
        "model_name": f"ci-upload-{uuid.uuid4().hex[:8]}",
        "model_version": "1",
        "model_type": "anomaly_detection",
        "description": "Uploaded by the serving-half integration proof",
        "artifact_uri": artifact_uri,
        "score_semantics": semantics,
        "feature_names": FEATURES,
    })


def _deploy(session, model_id):
    return requests.post(f"{_ml()}/api/models/{model_id}/deploy", headers=session["headers"],
                         timeout=300, json={"model_id": model_id, "environment": "production"})


@pytest.fixture(scope="module")
def linear_upload(session, tmp_path_factory):
    path = _save(linear_model.LogisticRegression(), tmp_path_factory.mktemp("linear") / "model")
    return _upload(session, path)


# --- the happy path -----------------------------------------------------------------------------

def test_an_admitted_upload_registers_loads_scores_and_deploys(session, linear_upload):
    r = _register(session, linear_upload)
    assert r.status_code == 201, f"register -> {r.status_code} {r.text[:400]}"
    model_id = r.json()["model_id"]

    r = _deploy(session, model_id)
    assert r.status_code == 200, f"deploy -> {r.status_code} {r.text[:400]}"

    r = requests.get(f"{_ml()}/api/models/{model_id}", headers=session["headers"], timeout=30)
    assert r.status_code == 200
    row = r.json()
    assert row["status"] == "production"
    assert row["provenance"] == "uploaded", "where it came from is recorded, never asked (dec 8)"
    assert row["detector"] == "sklearn", "the detector its declaration matched is recorded"
    assert row["compatibility_status"], "the registration gate judged it against this environment"


# --- the deployment gate refuses on what only loading can show ----------------------------------

def test_an_uploaded_tree_model_is_refused_at_the_load_probe(session, tmp_path):
    # MLflow saves a tree model as skops only when its author vouches for the tree type. That is the
    # author's assertion, and an uploaded artifact's assertions are what the platform does not take.
    uri = _upload(session, _save(ensemble.RandomForestClassifier(n_estimators=3, random_state=0),
                                 tmp_path / "model", trusted_types=["sklearn.tree._tree.Tree"]))
    r = _register(session, uri)
    assert r.status_code == 201, f"register -> {r.status_code} {r.text[:400]}"

    r = _deploy(session, r.json()["model_id"])
    assert r.status_code == 409, f"deploy -> {r.status_code} {r.text[:400]}"
    assert r.json()["detail"]["stage"] == "trust", (
        "tree structures are trusted only from the platform's own kernels (ADR-0027 dec 13)")


# --- the registration gate ----------------------------------------------------------------------

def test_a_semantics_outside_the_vocabulary_is_refused(session, linear_upload):
    r = _register(session, linear_upload, semantics="probably_fine")
    assert r.status_code == 422, f"register -> {r.status_code} {r.text[:400]}"


def test_a_model_without_declared_semantics_is_refused(session, linear_upload):
    r = _register(session, linear_upload, semantics=None)
    assert r.status_code == 422, "there is no default: what the numbers mean is declared (dec 5)"


def test_another_tenants_admitted_upload_is_refused(session, linear_upload):
    own = "tenant_" + session["company_id"].replace("-", "_")
    other = "tenant_" + str(uuid.uuid4()).replace("-", "_")
    r = _register(session, linear_upload.replace(own, other))
    assert r.status_code == 422, f"register -> {r.status_code} {r.text[:400]}"


def test_the_unjudged_staging_area_is_refused(session, linear_upload):
    own = "tenant_" + session["company_id"].replace("-", "_")
    staging = linear_upload.replace(f"{own}/uploads/", f"_upload-staging/{own}/")
    assert staging != linear_upload
    r = _register(session, staging)
    assert r.status_code == 422, f"register -> {r.status_code} {r.text[:400]}"


def test_no_session_mints_nothing(session):
    r = requests.post(f"{_ml()}/api/models/upload-capability", timeout=30)
    assert r.status_code == 401
