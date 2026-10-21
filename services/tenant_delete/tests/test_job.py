# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The job empties every store before the database, records each, and resumes (ADR-0033 dec 4).

The database is emptied last because it holds the record of what is still to remove; a store this
deployment has no step for keeps the deletion pending rather than being skipped or faked.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from tenant_delete import job

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
B = "11111111-2222-3333-4444-555555555555"
STORES = ["capability_store", "cold_layer", "broker"]
NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


class Registry:
    def __init__(self, pending, held=True, fail_complete_for=()):
        self._pending = pending
        self.held = held
        self.fail_complete_for = set(fail_complete_for)
        self.records, self.completed, self.calls = [], [], []

    @contextmanager
    def run_lock(self):
        yield self.held

    def required_stores(self):
        return list(STORES)

    def pending(self):
        return [(c, NOW, list(r)) for c, r in self._pending]

    def user_ids(self, company_id):
        return [f"user-of-{company_id}"]

    def record(self, company_id, store, removal):
        self.calls.append(("record", company_id, store))
        self.records.append((company_id, store, removal))

    def complete(self, company_id, backup_retention):
        self.calls.append(("complete", company_id))
        if company_id in self.fail_complete_for:
            raise RuntimeError("database unreachable")
        self.completed.append((company_id, backup_retention))
        return {"schema": f"tenant_{company_id}"}


def _step(name, seen):
    def step(company_id, users):
        seen.append((name, company_id, tuple(users)))
        return job.Removal(detail={"store": name})
    return step


def test_every_store_is_emptied_and_recorded_before_the_database():
    seen = []
    reg = Registry([(A, [])])
    steps = {s: _step(s, seen) for s in STORES}
    outcome = job.run(reg, steps, timedelta(days=14))
    assert [s for s, _, _ in seen] == STORES
    assert [c for c in reg.calls] == [("record", A, s) for s in STORES] + [("complete", A)]
    assert outcome.completed == [A] and reg.completed == [(A, timedelta(days=14))]
    assert seen[0][2] == (f"user-of-{A}",)


def test_a_resumed_run_repeats_no_recorded_store():
    seen = []
    reg = Registry([(A, ["capability_store", "cold_layer"])])
    job.run(reg, {s: _step(s, seen) for s in STORES}, timedelta(days=14))
    assert [s for s, _, _ in seen] == ["broker"]


def test_a_store_without_a_step_keeps_the_deletion_pending():
    reg = Registry([(A, [])])
    outcome = job.run(reg, {"capability_store": _step("capability_store", []),
                            "broker": _step("broker", [])}, timedelta(days=14))
    assert outcome.waiting == {A: ["cold_layer"]} and outcome.completed == []
    assert ("complete", A) not in reg.calls
    # The stores it could empty are recorded, so the wait is only for the missing one.
    assert [s for _, s, _ in reg.records] == ["capability_store", "broker"]


def test_one_tenants_failure_does_not_stop_the_others():
    reg = Registry([(A, []), (B, [])], fail_complete_for={A})
    outcome = job.run(reg, {s: _step(s, []) for s in STORES}, timedelta(days=14))
    assert list(outcome.failed) == [A] and outcome.completed == [B]


def test_a_held_lock_skips_the_run():
    reg = Registry([(A, [])], held=False)
    assert job.run(reg, {s: _step(s, []) for s in STORES}, timedelta(days=14)) is None
    assert reg.calls == []


def test_the_broker_step_records_when_its_retention_ends():
    removal = job.broker_step(timedelta(hours=168), clock=lambda: NOW)(A, [])
    assert removal.retained_until == NOW + timedelta(hours=168)
    assert removal.detail == {"retention_hours": 168.0}
