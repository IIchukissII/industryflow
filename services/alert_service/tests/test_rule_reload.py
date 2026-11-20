# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's rules that could not be read are unknown, not empty (ADR-0005 dec 3).

Live: one failed reload set a tenant's rules to [], so its readings were evaluated against nothing,
the batch was handled and its offsets committed: the alerts were lost for good.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import repository  # noqa: E402
from errors import RulesUnavailable, is_infrastructure_error  # noqa: E402
from rules_engine import RulesEngine  # noqa: E402

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
B = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
RULE = {"rule_id": "r1", "rule_type": "threshold"}
READING = {
    "value": 1.0,
    "sensor_id": "11111111-1111-1111-1111-111111111111",
    "equipment_id": "22222222-2222-2222-2222-222222222222",
    "site_id": "site-1",
    "time": "2026-11-20T10:00:00Z",
}


def test_a_failed_read_is_reported_unread_never_as_no_rules(monkeypatch):
    async def schemas(self):
        return [repository.tenancy.schema_name(A), repository.tenancy.schema_name(B)]

    async def rules(self, company_id):
        if company_id == B:
            raise ConnectionResetError("reset")
        return [RULE]

    monkeypatch.setattr(repository.AlertRepository, "get_tenant_schemas", schemas)
    monkeypatch.setattr(repository.AlertRepository, "get_active_rules", rules)
    loaded, unread = asyncio.run(repository.RuleRepository(None).load_all_tenant_rules())
    assert loaded == {A: [RULE]} and unread == {B}


def test_a_tenant_whose_reload_failed_keeps_the_rules_last_read():
    engine = RulesEngine({A: [RULE], B: [RULE]}, alert_repo=None)
    engine.update_rules({A: []}, unread={B})
    assert engine.tenant_rules == {A: [], B: [RULE]} and engine.unloaded == set()


def test_a_tenant_never_read_is_retried_not_evaluated_against_nothing():
    engine = RulesEngine({A: [RULE]}, alert_repo=None, unloaded={B})
    with pytest.raises(RulesUnavailable) as raised:
        asyncio.run(engine.evaluate(dict(READING, company_id=B), B))
    assert is_infrastructure_error(raised.value)        # the batch is rewound, its offsets not committed
    engine.update_rules({A: [RULE]}, unread={B})          # still unread: still unloaded
    assert engine.unloaded == {B}
    engine.update_rules({A: [RULE], B: []})               # read: no rules is now known
    assert engine.unloaded == set() and asyncio.run(engine.evaluate(dict(READING, company_id=B), B)) == []
