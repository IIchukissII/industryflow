# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Alert writes are idempotent under redelivery, across a worker restart (ADR-0005 dec 6).

The failure this guards: the consumer commits offsets only after a full batch, so a crash mid-batch
redelivers readings to a fresh process. With the cooldown held only in memory, that process fired
every alert again. Each "restart" below is a new RulesEngine over the same store.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
sys.path.insert(0, os.path.dirname(__file__))

import rules_engine  # noqa: E402
from fake_alert_store import FakeAlertStore  # noqa: E402
from rules_engine import RulesEngine, as_utc, deterministic_alert_id  # noqa: E402

CO = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
SENSOR = "11111111-1111-1111-1111-111111111111"
RULE = {"rule_id": "44444444-4444-4444-4444-444444444444", "name": "High temp",
        "detection_type": "threshold", "condition": "greater_than", "threshold": 100,
        "severity": "high", "sensor_id": SENSOR, "enabled": True}
T0 = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _reading(seconds, value=150.0):
    return {"sensor_id": SENSOR, "value": value, "timestamp": (T0 + timedelta(seconds=seconds)).isoformat()}


def _run(store, batch):
    engine = RulesEngine({CO: [RULE]}, alert_repo=store)
    return [a for r in batch for a in asyncio.run(engine.evaluate(r, CO))]


# A sustained breach over 20 minutes, one reading a minute; the default cooldown is 300 s.
BATCH = [_reading(60 * i) for i in range(20)]


def test_redelivered_batch_after_restart_writes_nothing():
    store = FakeAlertStore()
    fired = _run(store, BATCH)
    assert len(fired) == len(store.saved) == 4  # t = 0, 5, 10, 15 min

    replayed = _run(store, BATCH)  # fresh process, empty cache, same readings

    assert replayed == []
    assert len(store.saved) == 4


def test_partial_replay_then_new_readings_keeps_the_cooldown():
    store = FakeAlertStore()
    _run(store, BATCH[:7])  # crashed after t = 6 min; alerts at 0 and 5 min
    _run(store, BATCH)  # restart redelivers from the last commit, then carries on

    assert [a["triggered_at"] for a in store.saved] == [T0 + timedelta(minutes=m) for m in (0, 5, 10, 15)]


def test_alert_id_is_deterministic_and_triggered_at_is_event_time():
    store = FakeAlertStore()
    _run(store, BATCH[:1])
    a = store.saved[0]

    assert a["triggered_at"] == T0
    scope = (CO, RULE["rule_id"], SENSOR, None)
    assert a["alert_id"] == deterministic_alert_id(scope, T0)


def test_out_of_order_reading_inside_the_cooldown_is_suppressed():
    store = FakeAlertStore()
    _run(store, [_reading(600)])
    assert _run(store, [_reading(500)]) == []  # older event time, still within 300 s


def test_db_suppression_is_cached_so_a_persisting_breach_does_not_query_every_reading():
    class Counting(FakeAlertStore):
        calls = 0

        async def save_alert_once(self, alert, **kw):
            Counting.calls += 1
            return await super().save_alert_once(alert, **kw)

    store = Counting()
    _run(store, BATCH[:1])
    engine = RulesEngine({CO: [RULE]}, alert_repo=store)  # restart
    for r in BATCH[1:5]:
        asyncio.run(engine.evaluate(r, CO))

    assert Counting.calls == 2  # the first DB answer (prior_at) primes the cache


def test_retrain_recommendation_cooldown_survives_restart():
    model = "22222222-2222-2222-2222-222222222222"
    rule = {"rule_id": RULE["rule_id"], "name": "drift", "detection_type": "statistical",
            "enabled": True, "model_id": model, "severity": "medium"}

    class Repo(FakeAlertStore):
        async def get_retrain_signals(self, *a):
            return {"tp": 2, "fp": 8, "recent_drift_alerts": 3}

    kw = dict(precision_floor=0.6, min_labels=10, min_drift_alerts=2, precision_window_days=30,
              drift_lookback_hours=72, cooldown_seconds=86400)
    store = Repo()
    first = asyncio.run(RulesEngine({CO: [rule]}, alert_repo=store).evaluate_retrain_recommendations(CO, **kw))
    again = asyncio.run(RulesEngine({CO: [rule]}, alert_repo=store).evaluate_retrain_recommendations(CO, **kw))

    assert len(first) == 1 and again == []
    assert len(store.saved) == 1


def test_dedup_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(rules_engine, "DEDUP_CACHE_MAX", 3)
    engine = RulesEngine({CO: []}, alert_repo=FakeAlertStore())
    for i in range(10):
        alert = {"company_id": CO, "rule_id": f"r{i}", "sensor_id": SENSOR, "triggered_at": T0,
                 "detection_type": "threshold", "severity": "low", "message": "m"}
        assert asyncio.run(engine._emit(alert))

    assert len(engine._last_emit) == 3


def test_as_utc_normalises_naive_and_iso():
    assert as_utc("2026-10-11T12:00:00Z") == T0
    assert as_utc(datetime(2026, 10, 11, 12, 0)) == T0
    assert as_utc(T0.astimezone(timezone(timedelta(hours=2)))) == T0
