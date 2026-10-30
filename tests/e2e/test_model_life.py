# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A model's life, through the public edge (ADR-0026 dec 10): an artifact built where models are
authored is uploaded through the one mediated door, registered bound to a feature configuration,
deployed past the load-and-score gate and scored; the bound configuration then keeps its inputs.

ADR-0030 (admission), ADR-0027/0037 (the artifact declares, no author code), ADR-0041 (bound
configuration), ADR-0028 (declared semantics)."""
import os
import uuid

import pytest

from harness import equipment, eventually, pretty, reading, send_reading

SENSORS = ("temp_1", "flow_1")

# Built in the authoring image, which carries the serving stack's MLflow and scikit-learn: a model
# without tree storage, saved in skops form with its input signature (docs/architecture/model-upload.md).
BUILD = """
import numpy as np, pandas as pd, mlflow.sklearn
from mlflow.models import infer_signature
from sklearn.linear_model import LogisticRegression
X = pd.DataFrame(np.random.default_rng(0).normal(size=(400, 2)), columns=["temp_1_raw", "flow_1_raw"])
y = (X["temp_1_raw"] + X["flow_1_raw"] > 1.5).astype(int)
m = LogisticRegression().fit(X, y)
mlflow.sklearn.save_model(m, "/out/model", serialization_format="skops",
                          signature=infer_signature(X, m.predict_proba(X)))
"""


@pytest.fixture(scope="module")
def pump(tenant):
    return equipment(tenant["engineer"], "pump", f"e2e-pump-{tenant['run']}", SENSORS)


@pytest.fixture(scope="module")
def feature_config(tenant):
    return tenant["engineer"].post("/api/feature-configs", json={
        "equipment_type": "pump", "name": f"e2e-cfg-{tenant['run']}", "base_sensors": list(SENSORS),
        "transformations": [{"name": f"{s}_raw", "type": "identity", "sensor": s} for s in SENSORS],
    }).json()["id"]


@pytest.fixture(scope="module")
def artifact(env, tenant, settings):
    """The model's files, built in the authoring image into the run's work directory (mounted at the
    same path here and on the host)."""
    out = os.path.join(settings["IF_E2E_WORK"], f"model-{tenant['run']}")
    os.makedirs(out, exist_ok=True)
    os.chmod(out, 0o777)
    env.run("docker", "run", "--rm", "-u", "1000", "-v", f"{out}:/out", "--entrypoint", "python",
            settings["IF_E2E_AUTHORING_IMAGE"], "-c", BUILD)
    root = os.path.join(out, "model")
    files = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            files[os.path.relpath(path, root)] = open(path, "rb").read()
    assert "MLmodel" in files, f"the authoring image built no manifest: {sorted(files)}"
    return files


@pytest.fixture(scope="module")
def model(edge, tenant, feature_config, pump, artifact):
    """Uploaded through the edge as the upload wizard does, then registered bound to the configuration."""
    eng = tenant["engineer"]
    cap = eng.post("/api/models/upload-capability").json()
    door = {"Authorization": f"Bearer {cap['capability']}"}
    staged = edge.anonymous("/api/2.0/industryflow-upload/stage", method="POST", headers=door,
                            json={"files": sorted(artifact)})
    assert staged.status_code == 200, staged.text
    staged = staged.json()
    for member, url in staged["urls"].items():
        put = edge.client.put(url, content=artifact[member])
        assert put.status_code in (200, 204), f"PUT {member}: {put.status_code} {put.text[:300]}"
    committed = edge.anonymous("/api/2.0/industryflow-upload/commit", method="POST", headers=door,
                               json={"upload_id": staged["upload_id"]})
    assert committed.status_code == 200, committed.text
    # The capability is spent by the commit (ADR-0015 dec 4).
    again = edge.anonymous("/api/2.0/industryflow-upload/commit", method="POST", headers=door,
                           json={"upload_id": staged["upload_id"]})
    assert again.status_code == 401
    registered = eng.post("/api/models", json={
        "model_name": f"e2e-model-{tenant['run']}", "model_version": "1", "model_type": "logistic_regression",
        "feature_config_id": feature_config, "equipment_id": pump[0],
        "artifact_uri": committed.json()["artifact_uri"], "score_semantics": "anomaly_probability",
    }).json()
    return registered


def test_an_uploaded_model_is_deployed_and_scores_the_readings_its_rule_watches(settings, tenant, model, pump,
                                                                                device):
    """Deployed past the load-and-score gate (ADR-0030 dec 7), then scored as the product scores: an ML
    rule on the equipment, readings through the device edge, an alert from the worker's call."""
    eng = tenant["engineer"]
    mid = model.get("model_id") or model["id"]
    eng.post(f"/api/models/{mid}/deploy", json={"model_id": mid, "environment": "production"})
    eid, sensors = pump
    rule = eng.post("/api/alert-rules", json={
        "name": f"e2e-ml-{tenant['run']}", "detection_type": "ml", "model_id": mid, "equipment_id": eid,
        "anomaly_threshold": 0.5, "severity": "high"}).json()["rule"]
    rid = str(rule["rule_id"])

    def alerted():
        # Readings well inside the anomalous region, sent until the worker has the rule and scores them.
        for name, value in (("temp_1", 2.5), ("flow_1", 2.0)):
            assert send_reading(settings, device, reading(eid, sensors[name], value)).status_code == 202
        return [a for a in eng.get("/api/alerts", params={"limit": 50}).json() if str(a.get("rule_id")) == rid]

    fired = eventually(alerted, timeout=300, every=15, what="the ML rule fires on the uploaded model's score")
    assert fired[0]["detection_type"] == "ml", pretty(fired[0])


def test_a_bound_configuration_keeps_its_inputs_and_is_not_deleted(tenant, feature_config, model):
    eng = tenant["engineer"]
    edit = eng.put(f"/api/feature-configs/{feature_config}", json={"base_sensors": ["temp_1"]}, expect=(409,))
    assert model["model_name"] in edit.text
    eng.delete(f"/api/feature-configs/{feature_config}", expect=(409,))
    renamed = f"e2e-cfg-renamed-{uuid.uuid4().hex[:6]}"
    assert eng.put(f"/api/feature-configs/{feature_config}", json={"name": renamed}).json()["name"] == renamed


def test_another_tenant_cannot_reach_the_model(root, model):
    # The superuser's own tenant is another one: the model is not found there.
    mid = model.get("model_id") or model["id"]
    root.get(f"/api/models/{mid}", expect=(403, 404))
