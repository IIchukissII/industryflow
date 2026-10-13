# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert-rule write routes validate the rule as a whole (ADR-0031, ADR-0021).

A partial edit is checked against the stored rule with the edit applied, so it cannot leave a rule
the detector would skip. The app is driven with the tenant session replaced by a fake that holds
one stored rule and records the writes.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from dependencies import get_db_with_tenant
from users import current_active_user

RULE_ID = uuid.uuid4()
MODEL_ID = uuid.uuid4()


class _Row:
    def __init__(self, mapping):
        self._mapping = mapping

    def __getattr__(self, name):
        try:
            return self._mapping[name]
        except KeyError:
            raise AttributeError(name)


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class RuleStore:
    def __init__(self, stored):
        self.stored = stored
        self.writes = []

    async def execute(self, clause, params=None):
        sql = str(clause)
        if sql.lstrip().startswith(("INSERT", "UPDATE")):
            self.writes.append((sql, dict(params or {})))
            return _Result(_Row({**(self.stored or {}), **(params or {})}))
        return _Result(_Row(self.stored) if self.stored else None)

    async def commit(self):
        pass


def _stored(**over):
    row = {
        "rule_id": RULE_ID, "name": "r", "description": None, "sensor_pattern": None,
        "sensor_id": uuid.uuid4(), "equipment_id": None, "site_id": None,
        "detection_type": "threshold", "condition": "greater_than", "threshold": 10.0,
        "threshold_min": None, "threshold_max": None, "model_id": None, "anomaly_threshold": 0.85,
        "model_config": None, "severity": "medium", "priority": 0, "enabled": True,
    }
    row.update(over)
    return row


@pytest.fixture
def client_for():
    def make(stored):
        store = RuleStore(stored)

        async def _db():
            yield store

        main.app.dependency_overrides[get_db_with_tenant] = _db
        main.app.dependency_overrides[current_active_user] = lambda: SimpleNamespace(
            id=uuid.uuid4(), email="op@example.com", company_id=uuid.uuid4(), role="engineer",
            is_superuser=False, is_active=True)
        return TestClient(main.app), store

    yield make
    main.app.dependency_overrides.clear()


def test_switching_to_drift_without_a_model_is_refused_and_nothing_written(client_for):
    c, store = client_for(_stored())
    r = c.put(f"/api/alert-rules/{RULE_ID}", json={"detection_type": "statistical"})
    assert r.status_code == 422
    assert store.writes == []


def test_partial_edit_of_a_drift_rule_is_checked_against_the_stored_rule(client_for):
    c, store = client_for(_stored(detection_type="statistical", model_id=MODEL_ID, threshold=0.3))
    assert c.put(f"/api/alert-rules/{RULE_ID}", json={"threshold": 1.5}).status_code == 422
    r = c.put(f"/api/alert-rules/{RULE_ID}", json={"threshold": 0.4})
    assert r.status_code == 200
    sql, params = store.writes[-1]
    assert "threshold = :threshold" in sql and params["threshold"] == 0.4
    assert "detection_type" not in params  # only what was sent is written


def test_mode_switch_is_validated_too(client_for):
    c, store = client_for(_stored())
    r = c.patch(f"/api/alert-rules/{RULE_ID}/detection-mode", json={"detection_type": "ml"})
    assert r.status_code == 422 and store.writes == []
    r = c.patch(f"/api/alert-rules/{RULE_ID}/detection-mode",
                json={"detection_type": "ml", "model_id": str(MODEL_ID)})
    assert r.status_code == 200 and r.json()["previous_mode"] == "threshold"


def test_create_ignores_a_tenant_in_the_body(client_for):
    c, store = client_for(None)
    r = c.post("/api/alert-rules", json={
        "name": "n", "detection_type": "threshold", "severity": "low", "condition": "less_than",
        "threshold": 1.0, "sensor_id": str(uuid.uuid4()), "company_id": str(uuid.uuid4()),
    })
    assert r.status_code == 201
    sql, params = store.writes[-1]
    assert "company_id" not in sql and "company_id" not in params
    assert params["created_by"] == "op@example.com"


def test_create_refuses_vocabulary_the_table_rejects(client_for):
    c, store = client_for(None)
    r = c.post("/api/alert-rules", json={"name": "n", "severity": "warning"})
    assert r.status_code == 422 and store.writes == []


def test_edit_of_a_missing_rule_is_404(client_for):
    c, _ = client_for(None)
    assert c.put(f"/api/alert-rules/{RULE_ID}", json={"threshold": 1.0}).status_code == 404
