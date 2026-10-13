# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A reading the engine cannot evaluate raises MessageValidationError (dead-lettered, ADR-0005
dec 3) instead of returning [] — which the consumer took as "handled" and committed past."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
sys.path.insert(0, os.path.dirname(__file__))

from errors import MessageValidationError  # noqa: E402
from fake_alert_store import FakeAlertStore  # noqa: E402
from rules_engine import RulesEngine, parse_reading  # noqa: E402

CO = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
SENSOR = "11111111-1111-1111-1111-111111111111"
RULE = {"rule_id": "44444444-4444-4444-4444-444444444444", "name": "High temp",
        "detection_type": "threshold", "condition": "greater_than", "threshold": 100,
        "sensor_id": SENSOR, "enabled": True}


def _evaluate(reading, company_id=CO):
    engine = RulesEngine({CO: [RULE]}, alert_repo=FakeAlertStore())
    return asyncio.run(engine.evaluate(reading, company_id))


@pytest.mark.parametrize("reading, company_id, needle", [
    ({"value": 1.0}, CO, "sensor_id"),
    ({"sensor_id": SENSOR}, CO, "value"),
    ({"sensor_id": SENSOR, "value": None}, CO, "value"),
    ({"sensor_id": SENSOR, "value": "hot"}, CO, "value"),
    ({"sensor_id": SENSOR, "value": float("nan")}, CO, "finite"),
    ({"sensor_id": SENSOR, "value": True}, CO, "value"),
    ({"sensor_id": "temp-1", "value": 1.0}, CO, "sensor_id"),
    ({"sensor_id": SENSOR, "equipment_id": "eq-1", "value": 1.0}, CO, "equipment_id"),
    ({"sensor_id": SENSOR, "value": 1.0}, "acme", "company_id"),
    ({"sensor_id": SENSOR, "value": 1.0, "timestamp": "yesterday"}, CO, "timestamp"),
    (["not", "an", "object"], CO, "object"),
])
def test_unprocessable_reading_raises_validation_error(reading, company_id, needle):
    with pytest.raises(MessageValidationError, match=needle):
        _evaluate(reading, company_id)


def test_valid_reading_is_canonicalised():
    r = parse_reading({"sensor_id": SENSOR.upper(), "value": "150", "timestamp": "2026-10-13T12:00:00Z"}, CO)
    assert r["sensor_id"] == SENSOR
    assert r["value"] == 150.0
    assert r["equipment_id"] is None
    assert r["timestamp"] == "2026-10-13T12:00:00+00:00"


def test_valid_breach_still_alerts():
    alerts = _evaluate({"sensor_id": SENSOR, "value": 150, "timestamp": "2026-10-13T12:00:00Z"})
    assert len(alerts) == 1 and alerts[0]["actual_value"] == 150.0


def test_sensor_name_lookup_failure_propagates_so_the_reading_is_retried():
    class DbDown(FakeAlertStore):
        async def get_sensor_name(self, sensor_id, company_id):
            raise ConnectionRefusedError("db down")

    class Store:
        async def store_reading(self, **kw):
            raise AssertionError("must not store under the UUID while the DB is down")

    engine = RulesEngine({CO: [RULE]}, alert_repo=DbDown(), feature_store=Store())
    reading = {"sensor_id": SENSOR, "equipment_id": "22222222-2222-2222-2222-222222222222", "value": 1.0}
    with pytest.raises(ConnectionRefusedError):
        asyncio.run(engine.evaluate(reading, CO))
