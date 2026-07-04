# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for alert-rule validation, incl. the drift ('statistical') rule requirements
(ADR-0021). Pure pydantic — no DB.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from models import AlertRuleCreate  # noqa: E402

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
    with pytest.raises(ValueError):
        AlertRuleCreate(**_base(model_id=None))


def test_ml_rule_requires_model_id():
    with pytest.raises(ValueError):
        AlertRuleCreate(**_base(detection_type="ml", model_id=None))


def test_valid_statistical_rule_is_accepted():
    rule = AlertRuleCreate(**_base())
    assert rule.detection_type.value == "statistical"
    assert str(rule.model_id) == MODEL_ID
    assert rule.threshold == 0.3


def test_statistical_threshold_out_of_range_rejected():
    with pytest.raises(ValueError):
        AlertRuleCreate(**_base(threshold=1.5))


def test_statistical_threshold_optional():
    # Omitted threshold is allowed — the drift evaluator applies the service default.
    rule = AlertRuleCreate(**_base(threshold=None))
    assert rule.threshold is None


def test_threshold_rule_needs_no_model():
    rule = AlertRuleCreate(
        name="t", detection_type="threshold", severity="low",
        sensor_id=str(uuid.uuid4()), condition="greater_than", threshold=100.0,
    )
    assert rule.model_id is None
