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

- **Threshold / statistical** — evaluated directly from the reading (e.g. value over a limit,
  or deviation from a rolling baseline).
- **ML-based** — the worker calls the ML service's `/api/inference/predict`
  ([inference](ml-and-features.md)) and raises an alert when the returned anomaly score crosses
  the rule's threshold. These service-to-service calls authenticate with the shared
  `INTERNAL_SERVICE_TOKEN` (the worker has no user session), and the worker fails closed if it
  is unset.

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
