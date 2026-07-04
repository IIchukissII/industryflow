# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the scheduled model-drift evaluator (ADR-0021, rules_engine.py).

Pure: the ml-service /api/drift call is faked, so no network/DB/Redis. Async methods are
driven with asyncio.run() to avoid a pytest-asyncio dependency (matching the sync style of
the existing rules-engine tests).
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import rules_engine  # noqa: E402
from rules_engine import RulesEngine  # noqa: E402

MODEL_ID = "22222222-2222-2222-2222-222222222222"
RULE_ID = "33333333-3333-3333-3333-333333333333"


# --- fake aiohttp plumbing -------------------------------------------------

class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None, timeout=None):
        _FakeSession.last_payload = json
        return self._resp


def _patch_ml(monkeypatch, status, payload):
    resp = _FakeResp(status, payload)
    monkeypatch.setattr(rules_engine.aiohttp, "ClientSession", lambda *a, **k: _FakeSession(resp))


class _RecordingRepo:
    def __init__(self):
        self.saved = []

    async def save_alert(self, alert):
        self.saved.append(alert)


def _drift_rule(**over):
    rule = {
        "rule_id": RULE_ID,
        "name": "vpd-forecaster drift",
        "detection_type": "statistical",
        "enabled": True,
        "model_id": MODEL_ID,
        "threshold": 0.30,
        "severity": "high",
    }
    rule.update(over)
    return rule


def _ok(share, prediction_drifted=None):
    payload = {
        "status": "ok",
        "data_drift": {"drift_share": share, "n_drifted": 1, "n_columns": 3,
                       "drift_detected": share > 0.3, "per_column": {}},
        "prediction_drift": None,
    }
    if prediction_drifted is not None:
        payload["prediction_drift"] = {"column": "prediction_score", "drifted": prediction_drifted}
    return payload


# --- _evaluate_drift -------------------------------------------------------

def test_fires_statistical_alert_when_share_exceeds_threshold(monkeypatch):
    _patch_ml(monkeypatch, 200, _ok(0.42))
    engine = RulesEngine({}, alert_repo=None)
    alert = asyncio.run(engine._evaluate_drift(_drift_rule(), "co-1", 1440, 0.5))
    assert alert is not None
    assert alert["detection_type"] == "statistical"
    assert alert["condition"] == "drift"
    assert alert["anomaly_score"] == 0.42
    assert alert["threshold_value"] == 0.30
    assert alert["model_id"] == MODEL_ID
    assert "consider retraining" in alert["message"]
    # payload carried the rule threshold through to ml-service
    assert _FakeSession.last_payload["drift_share_threshold"] == 0.30


def test_no_alert_when_share_below_threshold(monkeypatch):
    _patch_ml(monkeypatch, 200, _ok(0.10))
    engine = RulesEngine({}, alert_repo=None)
    assert asyncio.run(engine._evaluate_drift(_drift_rule(), "co-1", 1440, 0.5)) is None


def test_prediction_drift_fires_even_if_data_share_low(monkeypatch):
    _patch_ml(monkeypatch, 200, _ok(0.05, prediction_drifted=True))
    engine = RulesEngine({}, alert_repo=None)
    alert = asyncio.run(engine._evaluate_drift(_drift_rule(), "co-1", 1440, 0.5))
    assert alert is not None
    assert "Prediction-output drift also detected" in alert["message"]


def test_unavailable_status_yields_no_alert(monkeypatch):
    _patch_ml(monkeypatch, 200, {"status": "unavailable", "reason": "no reference profile"})
    engine = RulesEngine({}, alert_repo=None)
    assert asyncio.run(engine._evaluate_drift(_drift_rule(), "co-1", 1440, 0.5)) is None


def test_http_404_yields_no_alert(monkeypatch):
    _patch_ml(monkeypatch, 404, {})
    engine = RulesEngine({}, alert_repo=None)
    assert asyncio.run(engine._evaluate_drift(_drift_rule(), "co-1", 1440, 0.5)) is None


def test_default_threshold_used_when_rule_has_none(monkeypatch):
    _patch_ml(monkeypatch, 200, _ok(0.6))
    engine = RulesEngine({}, alert_repo=None)
    rule = _drift_rule()
    del rule["threshold"]
    alert = asyncio.run(engine._evaluate_drift(rule, "co-1", 1440, 0.5))
    assert alert is not None            # 0.6 > default 0.5
    assert alert["threshold_value"] == 0.5


# --- evaluate_drift_rules --------------------------------------------------

def test_evaluate_drift_rules_selects_saves_and_dedups(monkeypatch):
    _patch_ml(monkeypatch, 200, _ok(0.9))
    repo = _RecordingRepo()
    rules = {
        "co-1": [
            _drift_rule(),
            {"rule_id": "x", "detection_type": "ml", "enabled": True, "model_id": MODEL_ID},  # ignored
            _drift_rule(rule_id="disabled", enabled=False),                                   # ignored
            {"rule_id": "no-model", "detection_type": "statistical", "enabled": True},        # ignored
        ]
    }
    engine = RulesEngine(rules, alert_repo=repo)

    first = asyncio.run(engine.evaluate_drift_rules("co-1", 1440, 0.5))
    assert len(first) == 1
    assert len(repo.saved) == 1
    assert repo.saved[0]["detection_type"] == "statistical"

    # Second immediate pass is suppressed by the cooldown dedup (_should_emit).
    second = asyncio.run(engine.evaluate_drift_rules("co-1", 1440, 0.5))
    assert second == []
    assert len(repo.saved) == 1


def test_evaluate_drift_rules_empty_when_no_statistical_rules():
    engine = RulesEngine({"co-1": [{"rule_id": "t", "detection_type": "threshold", "enabled": True}]},
                         alert_repo=_RecordingRepo())
    assert asyncio.run(engine.evaluate_drift_rules("co-1", 1440, 0.5)) == []
