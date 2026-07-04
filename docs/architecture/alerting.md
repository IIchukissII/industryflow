<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Alerting

How IndustryFlow detects and records alerts. Code under `services/alert_service/` is
authoritative; this is the shape.

## Two parts

- **Alert API** (`services/alert_service/api`, port 8001) — CRUD for `alert_rules` and read
  access to `alerts`, tenant-scoped. The browser manages rules and reviews history through it.
- **Detection worker** (`services/alert_service/worker`) — a Kafka consumer with no HTTP API
  that evaluates rules against the live stream and writes alerts.

## Rule types

- **Threshold** — evaluated directly from the reading (value over/under a limit or outside a
  range), per-event.
- **ML-based** — per-event; the worker calls the ML service's `/api/inference/predict`
  ([inference](ml-and-features.md)) and raises an alert when the returned anomaly score crosses
  the rule's threshold. These service-to-service calls authenticate with the shared
  `INTERNAL_SERVICE_TOKEN` (the worker has no user session), and the worker fails closed if it
  is unset.
- **Statistical (model drift)** — **windowed and periodic, not per-event**
  (**[ADR-0021](../../ADR/ADR-0021-model-drift-monitoring.md)**). A scheduled evaluator in the
  worker asks the ML service `/api/drift/evaluate` whether a model's recent input (and its own
  output) distribution has moved away from the training baseline, and raises an alert when the
  drifted-feature share crosses the rule's threshold. A drift alert means *"the world has moved
  — investigate / consider retraining"*, not *"the model is wrong"*. See
  [ML & feature engineering → Model drift](ml-and-features.md). Same delegation pattern and
  `INTERNAL_SERVICE_TOKEN` as ML rules; a `statistical` rule binds a `model_id` and a drift
  threshold, and reaching the per-reading path is a no-op (drift is the wrong timescale for it).

## Delivery & idempotency

The worker consumes Kafka **at-least-once** (manual commits, bounded retry, dead-letter — see
[stream processing](stream-processing.md) and
**[ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md)**). Because at-least-once means a
reading can be seen more than once, the worker **deduplicates / cools down** alerts: a repeat
condition keyed by `(company_id, rule_id, sensor_id, equipment_id)` is suppressed within
`ALERT_COOLDOWN_SECONDS`, so a replay or burst does not produce duplicate alerts. (Cooldown
state is per worker replica; a Redis-backed shared cooldown is a noted follow-up.)

## Tenant scoping

Both the API and the worker resolve the tenant from the verified `company_id` and operate only
within that tenant's schema (**[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**).
The worker reads feature configs and ML models from the tenant schema (granted SELECT by the
init scripts) to evaluate ML rules.
