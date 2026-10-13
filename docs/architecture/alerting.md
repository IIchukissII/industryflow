<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Alerting

How IndustryFlow detects and records alerts. Code under `services/alert_service/` is
authoritative; this is the shape.

## Two parts

- **HTTP surface: the API gateway** (`/api/alert-rules`, `/api/alerts`). It is the only one
  (ADR-0031): rule CRUD with whole-rule validation, history, acknowledge, operator labels and
  precision (ADR-0022), all tenant-scoped through the shared resolver (ADR-0003). The alert service
  has no HTTP API of its own.
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

## Operator feedback & retrain recommendation (ADR-0022)

Label-free drift says the input distribution moved; it can't say whether a model's alerts are
still *correct*. That needs a human verdict (**[ADR-0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md)**):

- **Labels.** An operator marks a fired alert `true_positive` / `false_positive` / `unsure`
  (`PATCH /api/alerts/{alert_id}/label` — distinct from acknowledge). Labels live in a dedicated
  per-tenant `alert_labels` table, **not** on the alerts hypertable, so they outlive its 90-day
  retention; the alert's rule/model/detection/severity are denormalized onto the label so it
  stays meaningful after the alert ages out.
- **Precision over time.** `GET /api/alerts/label-metrics` buckets the labels and returns
  **precision = TP/(TP+FP)** and the false-positive rate, per bucket and overall, optionally
  scoped to a model or rule. **Recall is intentionally absent** — fired-alert labels carry no
  false-negative signal, so a recall number would be fiction.
- **Retrain recommendation.** A scheduled evaluator in the worker (sibling of the drift
  evaluator) raises a distinct high-severity `statistical` alert, `condition='retrain_recommended'`,
  when a model shows **both** sustained drift **and** label-derived precision decay — model-scoped
  cooldown so it doesn't repeat every cycle. It *recommends* retraining (manual/notebook); it does
  not train, since no training service exists. Tunable via the `RETRAIN_*` config.
- **Storage.** `alert_labels` is created by init script `15-alert-labels.sql`, wired into
  `create_tenant_schema()` and backfilled onto existing tenants; the verdict is `CHECK`-constrained
  and `unsure` is excluded from the precision denominator. All of this is validated end-to-end on
  the compose deployment. Not built: a retrain flag beside the model on the Models page (the
  recommendation lands in alert history).
- **Operator UI.** The Alert History page carries a per-alert three-way label control (Real /
  False / Unsure) and a "Precision (labelled)" KPI, alongside the acknowledge action — so labelling
  and the resulting precision live where operators already triage alerts.

## Delivery & idempotency

The worker consumes Kafka **at-least-once** (manual commits, bounded retry, dead-letter — see
[stream processing](stream-processing.md) and
**[ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md)**). A reading that cannot be handled
is treated by *why*:

- **The reading is bad** — not JSON, no `company_id`, no `sensor_id` or numeric `value`, an id that
  is not a UUID, an unparseable time. No retry can fix it, so it goes straight to the dead-letter
  topic (`KAFKA_TOPIC_DLQ`, default `<sensor topic>.dlq`) with the error, and the batch commits.
- **The platform under it is down** — the database, its pool, the network. The reading is fine,
  so it is **never** dead-lettered: the batch is rewound to the last commit and retried, backing off
  from `KAFKA_RETRY_BACKOFF_SECONDS` and doubling up to `KAFKA_MAX_BACKOFF_SECONDS`, until the
  dependency is back.
- **Anything else** is retried `KAFKA_MAX_RETRIES` times, then dead-lettered.

A commit lost to a consumer-group rebalance rewinds the same way and carries on (the batch is
redelivered, and the writes are idempotent). Any other Kafka error ends the loop, and a loop that
ends — consumer, rule reload, ML or drift evaluation — makes the process exit non-zero, so it is
restarted rather than left running with nothing consuming. Liveness is a heartbeat file the
consumer touches on every poll; `healthcheck.py` fails once it is older than
`HEARTBEAT_MAX_AGE_SECONDS` (the compose healthcheck and the chart's exec liveness probe).

Dead letters are visible to operators: the chart's `IndustryFlowDeadLetteredReadings` rule fires
when any `*.dlq` topic grows (the kafka exporter's offset series). A dead-lettered reading raised
no alert, so it needs a look.

The worker publishes nothing else to Kafka: alerts are written to the tenant schema, which is where
the API and the UI read them. Because at-least-once means a
reading can be seen more than once, the worker **deduplicates / cools down** alerts: a repeat
condition keyed by `(company_id, rule_id, sensor_id, equipment_id)` is suppressed within
`ALERT_COOLDOWN_SECONDS` of its **event time**, so a replay or burst does not produce duplicate
alerts. Retrain recommendations are keyed by model, with their own `RETRAIN_*` cooldown.

The cooldown is enforced **in the database**, so it holds across restarts and replicas: the
insert writes only if no alert in the same key sits within the cooldown of the reading's event
time. Two writers in one key at once (two replicas' ML passes, or a redelivery racing the
original) would each see no prior row, so the insert first takes a transaction-scoped advisory
lock on the key; the second writer's check runs after the first commits and sees its row. The
`alert_id` is derived from the key and the event time, never random, so an exact replay collides
on the primary key and is dropped (`ON CONFLICT DO NOTHING`). An ML alert's event time is its
snapshot's newest reading, not the wall clock, so re-scoring the same snapshot is such a replay.
A bounded in-memory cache (`ALERT_DEDUP_CACHE_MAX`) in front of it spares the query while an
anomaly persists. No schema change was needed: the hypertable's time index bounds the lookup.

## ML snapshot

ML rules score an equipment's full sensor set, assembled from per-sensor arrivals in a Redis
latest-value store (`REDIS_URL`, required; the worker will not start without it). Keys are
`features:{company_id}:{equipment_id}:{sensor_name}`, so the tenant is part of the key. A sensor
whose latest reading is older than `FEATURE_MAX_AGE_SECONDS` (by its own event time) is treated as
missing, and an incomplete snapshot is not scored. When the ML service reports that it scored with
the stateful feature class neutralized (`degraded`, ADR-0024), the alert message says so and names
the features, so the operator labelling it sees a lower-confidence score.

## Tenant scoping

Both the API and the worker resolve the tenant from the verified `company_id` and operate only
within that tenant's schema. The API reads it from the JWT; the worker reads it from the Kafka
message, where ingestion stamped it from the device certificate's SAN (ADR-0002) (**[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**).
The worker reads feature configs and ML models from the tenant schema (granted SELECT by the
init scripts) to evaluate ML rules. Every tenant query runs in a transaction that sets the tenant
`search_path` with `SET LOCAL`, so a pooled connection never carries it past the query (ADR-0003).
