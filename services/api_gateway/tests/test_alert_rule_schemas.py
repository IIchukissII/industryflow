# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Alert-rule validation on the gateway, the one alerting HTTP surface (ADR-0031).

A model-bound rule (ML, or drift per ADR-0021) needs its model; a drift threshold is a share.
The vocabulary is the alert_rules table's: anything else is refused here, not by the database.
Pure pydantic, no DB.
"""
import uuid

import pytest
from pydantic import ValidationError

from schemas import AlertRuleCreate, AlertRuleUpdate, DetectionModeSwitch

MODEL_ID = str(uuid.uuid4())


def _base(**over):
    data = {
        "name": "r",
        "detection_type": "statistical",
        "severity": "high",
        "sensor_id": str(uuid.uuid4()),
        "model_id": MODEL_ID,
        "threshold": 0.3,
    }
    data.update(over)
    return data


def test_statistical_rule_requires_model_id():
    with pytest.raises(ValidationError):
        AlertRuleCreate(**_base(model_id=None))


def test_ml_rule_requires_model_id():
    with pytest.raises(ValidationError):
        AlertRuleCreate(**_base(detection_type="ml", model_id=None))


def test_valid_statistical_rule_is_accepted():
    rule = AlertRuleCreate(**_base())
    assert rule.detection_type.value == "statistical"
    assert str(rule.model_id) == MODEL_ID
    assert rule.threshold == 0.3


def test_statistical_threshold_out_of_range_rejected():
    with pytest.raises(ValidationError):
        AlertRuleCreate(**_base(threshold=1.5))


def test_statistical_threshold_optional():
    # Omitted threshold is allowed: the drift evaluator applies the service default.
    assert AlertRuleCreate(**_base(threshold=None)).threshold is None


def test_threshold_rule_needs_no_model():
    rule = AlertRuleCreate(
        name="t", detection_type="threshold", severity="low",
        sensor_id=str(uuid.uuid4()), condition="greater_than", threshold=100.0,
    )
    assert rule.model_id is None


@pytest.mark.parametrize("field,value", [
    ("severity", "warning"), ("severity", "info"),
    ("detection_type", "ml_model"), ("detection_type", "hybrid"), ("detection_type", "disabled"),
    ("condition", "approximately"),
])
def test_vocabulary_outside_the_table_is_refused(field, value):
    with pytest.raises(ValidationError):
        AlertRuleCreate(**_base(**{field: value}))


def test_tenant_in_body_never_reaches_a_column():
    cols = AlertRuleCreate(**_base(company_id=str(uuid.uuid4()))).columns()
    assert "company_id" not in cols


def test_model_config_is_carried_under_its_column_name():
    cols = AlertRuleCreate(**_base(model_config={"k": 1})).columns()
    assert cols["model_config"] == {"k": 1}


def test_update_carries_only_what_was_sent():
    assert AlertRuleUpdate(threshold=0.4).changes() == {"threshold": 0.4}


def test_mode_switch_requires_a_known_mode():
    with pytest.raises(ValidationError):
        DetectionModeSwitch(detection_type="hybrid")
