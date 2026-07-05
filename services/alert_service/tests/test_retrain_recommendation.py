# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the retrain-recommendation evaluator (ADR-0022 dec 4, rules_engine.py).

Pure: the DB read (get_retrain_signals) is faked, so no network/DB/Redis. Async methods are
driven with asyncio.run() to match the sync style of the existing rules-engine tests.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

from rules_engine import RulesEngine, should_recommend_retrain  # noqa: E402

MODEL_ID = "22222222-2222-2222-2222-222222222222"
RULE_ID = "33333333-3333-3333-3333-333333333333"

# Common thresholds: recommend below precision 0.6, needs >=10 labels and >=2 drift alerts.
KW = dict(
    precision_floor=0.6, min_labels=10, min_drift_alerts=2,
    precision_window_days=30, drift_lookback_hours=72,
    cooldown_seconds=86400, severity="high",
)


# --- the pure decision function --------------------------------------------

def test_recommends_when_precision_low_and_drift_sustained():
    # 2 TP / 8 FP = 0.2 precision, 3 drift alerts, 10 labels → recommend, returns the precision.
    assert should_recommend_retrain(2, 8, 3, precision_floor=0.6, min_labels=10, min_drift_alerts=2) == 0.2


def test_none_when_precision_at_or_above_floor():
    assert should_recommend_retrain(9, 1, 3, precision_floor=0.6, min_labels=10, min_drift_alerts=2) is None
    # Exactly at the floor is "still performing" — not below.
    assert should_recommend_retrain(6, 4, 3, precision_floor=0.6, min_labels=10, min_drift_alerts=2) is None


def test_none_when_not_enough_labels():
    # Low precision but only 3 labels — insufficient evidence, no fake recommendation.
    assert should_recommend_retrain(1, 2, 5, precision_floor=0.6, min_labels=10, min_drift_alerts=2) is None


def test_none_when_drift_not_sustained():
    # Precision decayed but only 1 drift alert — a bad-labels-only signal must not trigger.
    assert should_recommend_retrain(2, 8, 1, precision_floor=0.6, min_labels=10, min_drift_alerts=2) is None


# --- evaluate_retrain_recommendations --------------------------------------

class _Repo:
    def __init__(self, signals):
        self.signals = signals
        self.saved = []

    async def get_retrain_signals(self, model_id, company_id, precision_window_days, drift_lookback_hours):
        return self.signals

    async def save_alert(self, alert):
        self.saved.append(alert)


def _drift_rule(**over):
    rule = {
        "rule_id": RULE_ID,
        "name": "vpd-forecaster drift",
        "detection_type": "statistical",
        "enabled": True,
        "model_id": MODEL_ID,
        "severity": "medium",
    }
    rule.update(over)
    return rule


def test_recommends_and_saves_a_retrain_alert():
    repo = _Repo({"tp": 2, "fp": 8, "recent_drift_alerts": 3})
    engine = RulesEngine({"co-1": [_drift_rule()]}, alert_repo=repo)

    out = asyncio.run(engine.evaluate_retrain_recommendations("co-1", **KW))

    assert len(out) == 1 and len(repo.saved) == 1
    a = repo.saved[0]
    assert a["condition"] == "retrain_recommended"
    assert a["detection_type"] == "statistical"
    assert a["severity"] == "high"
    assert a["model_id"] == MODEL_ID
    assert a["actual_value"] == 0.2
    assert "Retrain recommended" in a["message"]


def test_model_cooldown_suppresses_second_pass():
    repo = _Repo({"tp": 2, "fp": 8, "recent_drift_alerts": 3})
    engine = RulesEngine({"co-1": [_drift_rule()]}, alert_repo=repo)

    asyncio.run(engine.evaluate_retrain_recommendations("co-1", **KW))
    second = asyncio.run(engine.evaluate_retrain_recommendations("co-1", **KW))

    assert second == []
    assert len(repo.saved) == 1


def test_recommends_once_per_model_across_rules():
    repo = _Repo({"tp": 2, "fp": 8, "recent_drift_alerts": 3})
    # Two enabled drift rules on the SAME model — one recommendation, not two.
    engine = RulesEngine({"co-1": [_drift_rule(), _drift_rule(rule_id="other")]}, alert_repo=repo)

    out = asyncio.run(engine.evaluate_retrain_recommendations("co-1", **KW))

    assert len(out) == 1 and len(repo.saved) == 1


def test_no_reco_when_precision_healthy():
    repo = _Repo({"tp": 9, "fp": 1, "recent_drift_alerts": 3})
    engine = RulesEngine({"co-1": [_drift_rule()]}, alert_repo=repo)

    assert asyncio.run(engine.evaluate_retrain_recommendations("co-1", **KW)) == []
    assert repo.saved == []


def test_no_reco_without_statistical_rules():
    repo = _Repo({"tp": 2, "fp": 8, "recent_drift_alerts": 3})
    engine = RulesEngine(
        {"co-1": [{"rule_id": "t", "detection_type": "threshold", "enabled": True}]},
        alert_repo=repo,
    )
    assert asyncio.run(engine.evaluate_retrain_recommendations("co-1", **KW)) == []
