# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the threshold rule evaluation (services/alert_service/worker/rules_engine.py).

Pure: constructs a RulesEngine with no repository/feature-store and exercises
_evaluate_threshold directly — no Kafka, DB, or Redis.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

from rules_engine import RulesEngine  # noqa: E402


def _engine():
    return RulesEngine({}, alert_repo=None)


def _sensor(value):
    return {
        "value": value,
        "sensor_id": "11111111-1111-1111-1111-111111111111",
        "equipment_id": "eq-1",
        "site_id": "site-1",
        "time": "2026-06-27T10:00:00Z",
    }


def test_greater_than_triggers_with_alert_fields():
    rule = {"rule_id": "r1", "name": "High temp", "condition": "greater_than",
            "threshold": 100, "severity": "high"}
    alert = _engine()._evaluate_threshold(_sensor(150), rule, "company-1")
    assert alert is not None
    assert alert["detection_type"] == "threshold"
    assert alert["condition"] == "greater_than"
    assert alert["actual_value"] == 150
    assert alert["threshold_value"] == 100
    assert alert["severity"] == "high"
    assert alert["company_id"] == "company-1"


def test_below_threshold_does_not_trigger():
    rule = {"rule_id": "r1", "name": "High temp", "condition": "greater_than", "threshold": 100}
    assert _engine()._evaluate_threshold(_sensor(50), rule, "company-1") is None


def test_less_than_triggers():
    rule = {"rule_id": "r2", "name": "Low pressure", "condition": "less_than", "threshold": 10}
    alert = _engine()._evaluate_threshold(_sensor(5), rule, "c")
    assert alert is not None and alert["condition"] == "less_than"


def test_legacy_threshold_max_format():
    rule = {"rule_id": "r3", "name": "Max", "threshold_max": 100}
    alert = _engine()._evaluate_threshold(_sensor(150), rule, "c")
    assert alert is not None
    assert alert["condition"] == "above_max"
    assert alert["threshold_value"] == 100


def test_severity_defaults_to_medium():
    rule = {"rule_id": "r4", "name": "T", "condition": "greater_than", "threshold": 1}
    alert = _engine()._evaluate_threshold(_sensor(2), rule, "c")
    assert alert["severity"] == "medium"
