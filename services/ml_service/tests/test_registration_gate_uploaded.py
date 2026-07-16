# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The registration gate, driven at the wire, for an externally-authored model (ADR-0030 dec 5/6/8).

The pure rule has its own tests. This asks the question those cannot: **does the gate actually call
it?** The hole this slice closes was exactly of that shape — the gate was keyed on a run and returned
"unjudged" without one, so an uploaded artifact (which has no run by design) would have passed
through it untouched while every unit test stayed green.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import extensions  # noqa: E402
import extensions.builtins_detectors  # noqa: F401,E402  (registers the built-ins)
import model_compat  # noqa: E402
import uploaded_model  # noqa: E402
from routers.models import router, get_company_id_dependency  # noqa: E402

CID = str(uuid.uuid4())
ARTIFACT = f"s3://mlflow/tenant_{CID.replace('-', '_')}/uploads/u1"


class FakeRepo:
    def __init__(self):
        self.created = []

    async def create_model(self, company_id, model_data):
        self.created.append((company_id, model_data))
        return uuid.uuid4()


def _compat(flavor="mlflow.sklearn", status=model_compat.COMPATIBLE):
    """A real Compatibility — it is frozen, so the flavor is constructed, not assigned. The gate's
    *verdict* is stubbed here on purpose: what is under test is whether the gate ASKS, not whether
    ADR-0027's comparison is right (it has its own tests, and its own artifact IO)."""
    return model_compat.Compatibility(status=status, flavor=flavor)


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_company_id_dependency] = lambda: CID
    repo = FakeRepo()
    app.state.ml_repository = repo

    async def fake_evaluate_uri(uri):
        return _compat()

    async def fake_evaluate_run(run_id):
        return _compat()

    monkeypatch.setattr(model_compat, "evaluate_uri", fake_evaluate_uri)
    monkeypatch.setattr(model_compat, "evaluate_run", fake_evaluate_run)
    c = TestClient(app)
    c.repo = repo
    return c


def _body(**over):
    body = {"model_name": "external", "model_version": "1", "model_type": "outlier"}
    body.update(over)
    return body


# --- provenance is read from how it arrived (dec 8) --------------------------------------------

def test_a_model_with_a_run_is_recorded_as_kernel_authored(client):
    r = client.post("/api/models", json=_body(mlflow_run_id="run-1"))
    assert r.status_code == 201, r.text
    _, stored = client.repo.created[0]
    assert stored["provenance"] == uploaded_model.PROVENANCE_KERNEL


def test_an_uploaded_model_is_recorded_as_uploaded_with_its_artifact(client):
    r = client.post("/api/models", json=_body(artifact_uri=ARTIFACT, score_semantics="outlier_score"))
    assert r.status_code == 201, r.text
    _, stored = client.repo.created[0]
    assert stored["provenance"] == uploaded_model.PROVENANCE_UPLOADED
    assert stored["artifact_uri"] == ARTIFACT


def test_a_caller_cannot_declare_its_own_provenance(client):
    """The one fact most worth lying about. A supplied `provenance` is not honoured — it is not a
    field, so it is ignored, and the derived value stands."""
    r = client.post("/api/models", json=_body(artifact_uri=ARTIFACT, score_semantics="outlier_score",
                                   provenance="kernel"))
    assert r.status_code == 201, r.text
    _, stored = client.repo.created[0]
    assert stored["provenance"] == uploaded_model.PROVENANCE_UPLOADED


# --- the gate ACTUALLY asks (the hole this slice closes) ---------------------------------------

def test_an_uploaded_model_without_declared_semantics_is_refused(client):
    r = client.post("/api/models", json=_body(artifact_uri=ARTIFACT))
    assert r.status_code == 422
    assert "will not guess" in str(r.json()["detail"])
    assert client.repo.created == []          # and nothing was registered


def test_an_uploaded_model_with_an_unknown_semantics_is_refused(client):
    r = client.post("/api/models", json=_body(artifact_uri=ARTIFACT, score_semantics="vibes"))
    assert r.status_code == 422
    assert client.repo.created == []


def test_an_uploaded_model_no_detector_can_score_is_refused(client, monkeypatch):
    async def unservable_flavor(uri):
        return _compat(flavor="mlflow.somethingnobodyhas")
    monkeypatch.setattr(model_compat, "evaluate_uri", unservable_flavor)
    r = client.post("/api/models", json=_body(artifact_uri=ARTIFACT, score_semantics="outlier_score"))
    assert r.status_code == 422
    assert "nothing in this deployment can score" in str(r.json()["detail"])
    assert client.repo.created == []


def test_an_uploaded_model_whose_declaration_holds_is_registered(client):
    r = client.post("/api/models", json=_body(artifact_uri=ARTIFACT, score_semantics="outlier_score"))
    assert r.status_code == 201, r.text
    assert len(client.repo.created) == 1


def test_a_model_with_neither_run_nor_artifact_is_refused_not_unjudged(client):
    """Before ADR-0030 this was "unjudged" and registered anyway. An unwatched model that nothing
    can even have an opinion about has no business in the registry."""
    r = client.post("/api/models", json=_body())
    assert r.status_code == 422
    assert client.repo.created == []


# --- a kernel-authored model is NOT held to the upload path's rules -----------------------------

def test_a_kernel_model_needs_no_declared_semantics(client):
    """ADR-0030 deliberately leaves the notebook path alone: its detector already declares the
    semantics (ADR-0028 dec 1), so demanding one here would be a refusal nothing decided for it."""
    r = client.post("/api/models", json=_body(mlflow_run_id="run-1"))
    assert r.status_code == 201, r.text


# --- the declaration is judged, not stored ------------------------------------------------------

def test_declared_semantics_is_not_persisted_as_a_second_authority(client):
    # The detector remains the authority on what an output means (ADR-0028 dec 1); a per-model copy
    # could only drift from it.
    client.post("/api/models", json=_body(artifact_uri=ARTIFACT, score_semantics="outlier_score"))
    _, stored = client.repo.created[0]
    assert "score_semantics" not in stored
