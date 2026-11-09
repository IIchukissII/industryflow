# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The dedup scenarios of AlertRepository.save_alert_once, defined once (ADR-0005 dec 6).

Each scenario is a sequence of save_alert_once calls on an empty tenant, with the outcome each call
must return. test_fake_alert_store.py runs every scenario against FakeAlertStore;
infrastructure/timescaledb/tests/test_alert_dedup_hypertable.py runs every scenario through the
worker's own AlertRepository on a real TimescaleDB. The fake is thereby held to the SQL it stands in
for (canon dec 7, ADR-0026 dec 11).

Stdlib only: both the alert_service unit job and the db-tenant-isolation job import it by path.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

WINDOW = 300  # seconds; the scenarios' usual cooldown
TICK = timedelta(microseconds=1)  # timestamptz resolution


@dataclass(frozen=True)
class Save:
    """One save_alert_once call and the (inserted, prior_at) it must return.

    scope is (rule, sensor, equipment, model): each label names one id per run, None stays NULL, and
    a model label makes it a retrain recommendation (retrain=True). at and prior are event times
    from the scenario's base, as timedeltas. alert names the alert_id: one label is one id; None is a
    fresh id.
    """
    scope: tuple
    at: timedelta
    inserted: bool
    prior: Optional[timedelta] = None
    window: int = WINDOW
    alert: Optional[str] = None


@dataclass(frozen=True)
class Acknowledge:
    """An operator acknowledges every retrain recommendation for the model labelled in scope."""
    scope: tuple


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: tuple
    # 'inside' sits well inside one hypertable chunk; 'chunk_boundary' puts the base on the edge
    # between two chunks. The fake has no chunks, so its suite takes any base for both.
    base: str = "inside"


def s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


A = ("rule-a", "sensor-a", None, None)            # an alert on a sensor
RETRAIN = (None, None, None, "model-a")           # a retrain recommendation for a model

SCENARIOS = (
    # --- the window ----------------------------------------------------------------------------
    Scenario("inside the window is suppressed, reporting the prior", (
        Save(A, s(0), True),
        Save(A, s(100), False, s(0)),
    )),
    Scenario("outside the window is a new alert", (
        Save(A, s(0), True),
        Save(A, s(400), True),
    )),
    Scenario("exactly one window later is not within it", (
        Save(A, s(0), True),
        Save(A, s(WINDOW), True),
    )),
    Scenario("exactly one window earlier is not within it", (
        Save(A, s(WINDOW), True),
        Save(A, s(0), True),
    )),
    Scenario("one tick inside the window is within it", (
        Save(A, s(0), True),
        Save(A, s(WINDOW) - TICK, False, s(0)),
        Save(A, -s(WINDOW) + TICK, False, s(0)),
    )),
    Scenario("an earlier event inside the window is suppressed", (
        Save(A, s(200), True),
        Save(A, s(0), False, s(200)),
    )),
    Scenario("a zero window suppresses nothing, even at the same instant", (
        Save(A, s(0), True, window=0),
        Save(A, s(0), True, window=0),
    )),
    Scenario("between two priors the later is reported", (
        Save(A, s(0), True),
        Save(A, s(400), True),
        Save(A, s(200), False, s(400)),
    )),
    Scenario("the window is the caller's, not the one stored rows were saved with", (
        Save(A, s(0), True),
        Save(A, s(400), True),
        Save(A, s(900), False, s(400), window=1000),
        Save(A, s(900), True, window=100),
    )),
    # --- replay --------------------------------------------------------------------------------
    Scenario("an exact replay is a primary-key no-op", (
        Save(A, s(0), True, window=0, alert="x"),
        Save(A, s(0), False, window=0, alert="x"),
    )),
    Scenario("an exact replay inside a window is suppressed by the window first", (
        Save(A, s(0), True, alert="x"),
        Save(A, s(0), False, s(0), alert="x"),
    )),
    Scenario("the same alert id at another event time is another row", (
        Save(A, s(0), True, window=0, alert="x"),
        Save(A, s(1), True, window=0, alert="x"),
    )),
    Scenario("an exact replay in another scope still hits the primary key", (
        Save(A, s(0), True, alert="x"),
        Save(("rule-b", "sensor-a", None, None), s(0), False, alert="x"),
    )),
    # --- the scope -----------------------------------------------------------------------------
    Scenario("another rule on the same sensor is another scope", (
        Save(A, s(0), True),
        Save(("rule-b", "sensor-a", None, None), s(10), True),
    )),
    Scenario("another sensor under the same rule is another scope", (
        Save(A, s(0), True),
        Save(("rule-a", "sensor-b", None, None), s(10), True),
    )),
    Scenario("an equipment and none are different scopes", (
        Save(("rule-a", "sensor-a", "equipment-a", None), s(0), True),
        Save(A, s(10), True),
        Save(("rule-a", "sensor-a", "equipment-a", None), s(20), False, s(0)),
        Save(A, s(30), False, s(10)),
    )),
    Scenario("a scope with no sensor still deduplicates", (
        Save(("rule-a", None, "equipment-a", None), s(0), True),
        Save(("rule-a", None, "equipment-a", None), s(30), False, s(0)),
        Save(("rule-b", None, "equipment-b", None), s(0), True),
    )),
    Scenario("a scope of all NULLs deduplicates with itself", (
        Save((None, None, None, None), s(0), True),
        Save((None, None, None, None), s(30), False, s(0)),
    )),
    # --- retrain recommendations ---------------------------------------------------------------
    Scenario("a retrain recommendation is suppressed within its model's window", (
        Save(RETRAIN, s(0), True),
        Save(RETRAIN, s(10), False, s(0)),
        Acknowledge(RETRAIN),
        Save(RETRAIN, s(400), True),
    )),
    # An open recommendation marks its model until acknowledged; a repeat would add only another
    # mark to clear (ADR-0022 dec 4).
    Scenario("an unacknowledged recommendation suppresses past the window", (
        Save(RETRAIN, s(0), True),
        Save(RETRAIN, s(400), False, s(0)),
        Save(RETRAIN, s(100_000), False, s(0)),
    )),
    Scenario("an acknowledged recommendation suppresses only within the window", (
        Save(RETRAIN, s(0), True),
        Acknowledge(RETRAIN),
        Save(RETRAIN, s(100), False, s(0)),
        Save(RETRAIN, s(400), True),
        Save(RETRAIN, s(100_000), False, s(400)),
    )),
    Scenario("an open recommendation for another model suppresses nothing", (
        Save((None, None, None, "model-b"), s(0), True),
        Save(RETRAIN, s(400), True),
    )),
    Scenario("an open alert does not hold back a recommendation or an alert outside the window", (
        Save(A, s(0), True),
        Save(RETRAIN, s(10), True),
        Save(A, s(400), True),
    )),
    Scenario("another model's recommendation is another scope", (
        Save(RETRAIN, s(0), True),
        Save((None, None, None, "model-b"), s(10), True),
    )),
    Scenario("an alert and a retrain recommendation never suppress each other", (
        Save(A, s(0), True),
        Save(RETRAIN, s(0), True),
        Save(RETRAIN, s(10), False, s(0)),
    )),
    Scenario("a recommendation does not suppress an all-NULL alert scope", (
        Save(RETRAIN, s(0), True),
        Save((None, None, None, None), s(10), True),
        Save(RETRAIN, s(20), False, s(0)),
    )),
    Scenario("a recommendation without a model never matches a prior one", (
        Save((None, None, None, ""), s(0), True),
        Save((None, None, None, ""), s(10), True),
    )),
    # --- the hypertable's chunks ---------------------------------------------------------------
    Scenario("the window reaches into the previous chunk", (
        Save(A, s(-60), True),
        Save(A, s(60), False, s(-60)),
        Save(A, s(400), True),
    ), base="chunk_boundary"),
    Scenario("an out-of-order event lands in the previous chunk and is suppressed", (
        Save(A, s(60), True),
        Save(A, s(-60), False, s(60)),
    ), base="chunk_boundary"),
)


def is_retrain(scope: tuple) -> bool:
    return scope[3] is not None


def materialise(scenario: Scenario, base: datetime, company_id: str):
    """[(alert, window, retrain, (inserted, prior_at))] for one run, with fresh ids per label.

    The empty model label "" stands for a recommendation whose model_id is NULL.
    """
    ids: dict = {}

    def ident(label):
        if label is None or label == "":
            return None
        return ids.setdefault(label, str(uuid.uuid4()))

    calls = []
    for step in scenario.steps:
        if isinstance(step, Acknowledge):
            calls.append((ident(step.scope[3]), None, None, None))
            continue
        rule, sensor, equipment, model = (ident(p) for p in step.scope)
        retrain = is_retrain(step.scope)
        alert = {
            "company_id": company_id,
            "alert_id": ident(f"alert:{step.alert}") if step.alert else str(uuid.uuid4()),
            "triggered_at": base + step.at,
            "rule_id": rule, "sensor_id": sensor, "equipment_id": equipment, "model_id": model,
            "condition": "retrain_recommended" if retrain else None,
            "detection_type": "statistical" if retrain else "threshold",
            "severity": "high", "message": scenario.name,
            "degraded": False, "neutralized_features": [],
        }
        expected = (step.inserted, None if step.prior is None else base + step.prior)
        calls.append((alert, timedelta(seconds=step.window), retrain, expected))
    return calls


async def run(store, scenario: Scenario, base: datetime, company_id: str, acknowledge=None):
    """(got, want): what store.save_alert_once returned for each step, and what it must return.

    An Acknowledge step calls acknowledge(model_id), by default the store's own.
    """
    acknowledge = acknowledge or store.acknowledge_recommendations
    got, want = [], []
    for alert, window, retrain, expected in materialise(scenario, base, company_id):
        if window is None:
            await acknowledge(alert)
            continue
        got.append(tuple(await store.save_alert_once(alert, window=window, retrain=retrain)))
        want.append(expected)
    return got, want
