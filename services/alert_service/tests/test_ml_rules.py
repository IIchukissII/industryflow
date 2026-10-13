# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Equipment-level ML rules: the degraded-serving marker (ADR-0024 dec 5) reaches the alert, and a
stale snapshot is not scored. The ml-service call and the repository reads are faked."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
sys.path.insert(0, os.path.dirname(__file__))

import rules_engine  # noqa: E402
from fake_alert_store import FakeAlertStore  # noqa: E402
from feature_store import FeatureStore  # noqa: E402
from rules_engine import RulesEngine  # noqa: E402
from test_drift_rules import _FakeResp, _FakeSession  # noqa: E402

CO = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
EQ = "22222222-2222-2222-2222-222222222222"
MODEL = "33333333-3333-3333-3333-333333333333"
RULE = {"rule_id": "44444444-4444-4444-4444-444444444444", "name": "pump anomaly",
        "detection_type": "ml", "enabled": True, "equipment_id": EQ, "model_id": MODEL,
        "anomaly_threshold": 0.5, "severity": "high"}


class Repo(FakeAlertStore):
    async def get_model_by_id(self, model_id, company_id):
        return {"feature_config_id": "55555555-5555-5555-5555-555555555555"}

    async def get_feature_config(self, config_id, company_id):
        return {"base_sensors": ["temp", "pressure"]}


def _patch_ml(monkeypatch, payload):
    calls = []
    resp = _FakeResp(200, payload)

    def session(*a, **k):
        calls.append(1)
        return _FakeSession(resp)

    monkeypatch.setattr(rules_engine.aiohttp, "ClientSession", session)
    return calls


def _engine(readings_age_s):
    fs = FeatureStore(use_memory=True)
    now = datetime.now(timezone.utc)
    for name, age in readings_age_s.items():
        asyncio.run(fs.store_reading(CO, EQ, name, (now - timedelta(seconds=age)).isoformat(), 1.0))
    repo = Repo()
    return RulesEngine({CO: [RULE]}, alert_repo=repo, feature_store=fs, feature_max_age_seconds=300), repo


def test_degraded_inference_is_marked_on_the_alert(monkeypatch):
    _patch_ml(monkeypatch, {"is_anomaly": True, "prediction": 0.9, "degraded": True,
                            "neutralized_features": ["temp_dev_from_mean"]})
    engine, repo = _engine({"temp": 5, "pressure": 5})

    alerts = asyncio.run(engine.evaluate_equipment_ml_rules(EQ, CO))

    assert len(alerts) == 1
    saved = repo.saved[0]
    assert "DEGRADED" in saved["message"] and "temp_dev_from_mean" in saved["message"]
    assert saved["degraded"] is True
    assert saved["neutralized_features"] == ["temp_dev_from_mean"]


def test_healthy_inference_carries_no_marker(monkeypatch):
    _patch_ml(monkeypatch, {"is_anomaly": True, "prediction": 0.9, "degraded": False,
                            "neutralized_features": None})
    engine, repo = _engine({"temp": 5, "pressure": 5})
    asyncio.run(engine.evaluate_equipment_ml_rules(EQ, CO))
    assert "DEGRADED" not in repo.saved[0]["message"]
    assert repo.saved[0]["degraded"] is False


def test_stale_sensor_means_no_inference(monkeypatch):
    calls = _patch_ml(monkeypatch, {"is_anomaly": True, "prediction": 0.9})
    engine, repo = _engine({"temp": 5, "pressure": 3600})  # pressure stopped reporting an hour ago

    assert asyncio.run(engine.evaluate_equipment_ml_rules(EQ, CO)) == []
    assert calls == []
    assert repo.saved == []


def test_ml_alert_event_time_is_the_snapshot_time_so_a_re_evaluation_dedups(monkeypatch):
    _patch_ml(monkeypatch, {"is_anomaly": True, "prediction": 0.9})
    engine, repo = _engine({"temp": 30, "pressure": 10})

    asyncio.run(engine.evaluate_equipment_ml_rules(EQ, CO))
    again = RulesEngine({CO: [RULE]}, alert_repo=repo, feature_store=engine.feature_store,
                        feature_max_age_seconds=300, cooldown_seconds=0)  # another replica, no cooldown

    assert asyncio.run(again.evaluate_equipment_ml_rules(EQ, CO)) == []  # same alert_id: PK no-op
    assert len(repo.saved) == 1
