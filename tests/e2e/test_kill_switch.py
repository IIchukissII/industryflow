# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The stateful-feature kill-switch, through the public edge (ADR-0026 dec 10).

A deployed model reads one stateful feature, the mean of its sensor's latest closed window. With the
switch on, its alerts are ordinary once that window exists. A platform superuser turns the switch off
through the gateway (no tenant administrator can), the flip is recorded with who made it, a repeat
is not, and the alerts raised meanwhile record that their score was degraded and which feature; a
label on one is counted apart from precision. The switch is turned back on at the end, whatever
happened.

ADR-0024 (the switch), ADR-0043 (who flips it, the record), ADR-0044 dec 3 and ADR-0022 dec 2-4
(the degraded alert as data).
"""
import pytest

from harness import build_model, equipment, eventually, pretty, reading, send_reading, upload_model

SWITCH = "/api/platform/switches/stateful_features_enabled"
SENSORS = ("temp_1", "flow_1")
MEAN = "temp_1_mean"

# Built in the authoring image: the score depends on the two readings; the window mean is an input
# the model was trained with, so it is a slot the kill-switch fills.
BUILD = """
import numpy as np, pandas as pd, mlflow.sklearn
from mlflow.models import infer_signature
from sklearn.linear_model import LogisticRegression
X = pd.DataFrame(np.random.default_rng(1).normal(size=(400, 3)), columns=["temp_1_raw", "flow_1_raw", "temp_1_mean"])
y = (X["temp_1_raw"] + X["flow_1_raw"] > 1.5).astype(int)
m = LogisticRegression().fit(X, y)
mlflow.sklearn.save_model(m, "/out/model", serialization_format="skops",
                          signature=infer_signature(X, m.predict_proba(X)))
"""


@pytest.fixture(scope="module")
def pump(tenant):
    return equipment(tenant["engineer"], "pump", f"e2e-ks-pump-{tenant['run']}", SENSORS)


@pytest.fixture(scope="module")
def model(env, edge, settings, tenant, pump):
    eng = tenant["engineer"]
    config = eng.post("/api/feature-configs", json={
        "equipment_type": "pump", "name": f"e2e-ks-cfg-{tenant['run']}", "base_sensors": list(SENSORS),
        "transformations": [{"name": f"{s}_raw", "type": "identity", "sensor": s} for s in SENSORS] + [
            {"name": MEAN, "type": "statistical", "sensor": "temp_1",
             "params": {"stat_type": "window_mean", "granularity": "1min"}}],
    }).json()["id"]
    files = build_model(env, settings, f"ks-model-{tenant['run']}", BUILD)
    registered = eng.post("/api/models", json={
        "model_name": f"e2e-ks-model-{tenant['run']}", "model_version": "1", "model_type": "logistic_regression",
        "feature_config_id": config, "equipment_id": pump[0],
        "artifact_uri": upload_model(edge, eng, files), "score_semantics": "anomaly_probability",
    }).json()
    mid = registered.get("model_id") or registered["id"]
    eng.post(f"/api/models/{mid}/deploy", json={"model_id": mid, "environment": "production"})
    return mid


@pytest.fixture(scope="module")
def switch(root):
    """The switch as found, and on again at the end whatever the journey did."""
    [found] = [s for s in root.get("/api/platform/switches").json() if s["key"] == "stateful_features_enabled"]
    assert found["value"] is True, "the switch must start on: another run left it off"
    yield
    root.put(SWITCH, json={"value": True})


def _alerts_of(settings, device, eng, pump, rule_id):
    """Readings well inside the anomalous region, sent once; the alerts of ``rule_id`` so far."""
    eid, sensors = pump
    for name, value in (("temp_1", 2.5), ("flow_1", 2.0)):
        assert send_reading(settings, device, reading(eid, sensors[name], value)).status_code == 202
    return [a for a in eng.get("/api/alerts", params={"limit": 100}).json() if str(a.get("rule_id")) == rule_id]


def _rule(eng, tenant, model, pump, phase):
    return str(eng.post("/api/alert-rules", json={
        "name": f"e2e-ks-{phase}-{tenant['run']}", "detection_type": "ml", "model_id": model,
        "equipment_id": pump[0], "anomaly_threshold": 0.5, "severity": "high"}).json()["rule"]["rule_id"])


def test_the_kill_switch_degrades_alerts_until_it_is_turned_back_on(settings, root, tenant, device, model, pump,
                                                                    switch):
    eng = tenant["engineer"]

    # Switch on: readings until the sensor's first 1-minute window has closed and been written (its
    # width plus allowed lateness), the aggregate the stateful feature reads. A rule made before then
    # would fire degraded first and its cooldown would hold the ordinary alert back.
    eid, sensors = pump

    def window_written():
        for name, value in (("temp_1", 2.5), ("flow_1", 2.0)):
            assert send_reading(settings, device, reading(eid, sensors[name], value)).status_code == 202
        return [w for w in eng.get("/api/aggregations/1min/latest").json() if str(w["sensor_id"]) == sensors["temp_1"]]

    eventually(window_written, timeout=600, every=20, what="the first closed 1-minute window of temp_1")
    on = _rule(eng, tenant, model, pump, "on")

    def ordinary():
        return [a for a in _alerts_of(settings, device, eng, pump, on) if a["degraded"] is False]

    first = eventually(ordinary, timeout=300, every=15, what="an alert computed with the window mean")[0]
    assert first["neutralized_features"] == [], pretty(first)
    eng.delete(f"/api/alert-rules/{on}")

    # Only a platform superuser flips it (ADR-0043 dec 1).
    tenant["admin"].put(SWITCH, json={"value": False}, expect=(403,))
    before = root.get(f"{SWITCH}/changes").json()
    flipped = root.put(SWITCH, json={"value": False}).json()
    assert (flipped["value"], flipped["changed"]) == (False, True), pretty(flipped)
    again = root.put(SWITCH, json={"value": False}).json()
    assert again["changed"] is False
    changes = root.get(f"{SWITCH}/changes").json()
    assert len(changes) == len(before) + 1, "a flip is recorded once, a repeat not at all (ADR-0043 dec 4, 5)"
    me = root.get("/users/me").json()
    assert (changes[0]["old_value"], changes[0]["new_value"], str(changes[0]["changed_by"])) == (True, False, str(me["id"]))

    # Switch off: the alerts raised now say their score was degraded, and which feature.
    off = _rule(eng, tenant, model, pump, "off")

    def degraded():
        return [a for a in _alerts_of(settings, device, eng, pump, off) if a["degraded"] is True]

    alert = eventually(degraded, timeout=300, every=15, what="an alert scored with the window mean neutralized")[0]
    assert alert["neutralized_features"] == [MEAN], pretty(alert)

    # A label on it is counted apart from precision (ADR-0022 dec 3).
    eng.patch(f"/api/alerts/{alert['alert_id']}/label", json={"verdict": "false_positive"})
    metrics = eng.get("/api/alerts/label-metrics", params={"model_id": model}).json()
    assert (metrics["degraded"]["false_positive"], metrics["overall"]["false_positive"]) == (1, 0), pretty(metrics)

    # Back on, and recorded.
    assert root.put(SWITCH, json={"value": True}).json()["changed"] is True
    assert root.get(f"{SWITCH}/changes").json()[0]["new_value"] is True
