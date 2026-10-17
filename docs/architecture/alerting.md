<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Alerting

The alert API, the detection worker, rule types, deduplication and operator labels. Code in
`services/alert_service/` and `services/api_gateway/routers/alert_*.py` is authoritative.

| Decision | ADR |
|---|---|
| The API gateway is the only alerting HTTP surface | [ADR-0031](../../ADR/ADR-0031-alerting-http-surface-single-owner.md) |
| At-least-once consumption, dead-letter | [ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md) |
| Tenant resolution | [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
| Model drift (`statistical` rules) | [ADR-0021](../../ADR/ADR-0021-model-drift-monitoring.md) |
| Operator labels, precision, retrain recommendation | [ADR-0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md) |
| Redis latest-value snapshot for ML rules | [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md) |
| Degraded scores under the stateful-feature kill-switch | [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md) |

## Components

| Component | Role | Code |
|---|---|---|
| api-gateway | Rule CRUD, alert history, acknowledge, labels, precision; tenant from the JWT | `services/api_gateway/routers/alert_rules.py`, `alerts_history.py` |
| alert-service-detector | Kafka consumer on `sensor-data-raw`; evaluates rules, writes `alerts`; no HTTP API | `services/alert_service/worker/` |
| ml-service-api | Scores ML rules and computes drift for the worker | [ml-and-features.md](ml-and-features.md) |
| Alert History page | Acknowledge, label (Real / False / Unsure), "Precision (labelled)" KPI | `services/frontend/src/pages/AlertHistory.jsx` |

## Endpoints (api-gateway)

| Method | Path | Purpose |
|---|---|---|
| GET, POST | `/api/alert-rules` | List, create |
| GET, PUT, DELETE | `/api/alert-rules/{rule_id}` | Read, replace, delete |
| PATCH | `/api/alert-rules/{rule_id}/detection-mode` | Change detection type |
| GET | `/api/alerts` | History |
| GET | `/api/alerts/unacknowledged`, `/api/alerts/critical` | Filtered history |
| PATCH | `/api/alerts/{alert_id}/acknowledge` | Acknowledge |
| PATCH | `/api/alerts/{alert_id}/label` | `true_positive` / `false_positive` / `unsure` |
| GET | `/api/alerts/label-metrics` | Precision = TP/(TP+FP) and false-positive rate per bucket and overall; optional model or rule scope |

## Rule types (`alert_rules.detection_type`)

| Type | Evaluated | How |
|---|---|---|
| `threshold` | Per reading | Value over / under `threshold`, or outside `threshold_min`–`threshold_max` |
| `ml` | Per reading | Worker calls `POST /api/inference/predict`; alert when the score crosses `anomaly_threshold` |
| `statistical` | Every `DRIFT_EVAL_INTERVAL` over `DRIFT_WINDOW_MINUTES` | Worker calls `POST /api/drift/evaluate` for the rule's `model_id`; alert when the drifted-feature share crosses the rule threshold; no-op per reading |
| Retrain recommendation | Scheduled, beside drift | `statistical` alert with `condition='retrain_recommended'` when a model shows sustained drift and precision below `RETRAIN_PRECISION_FLOOR`; cooldown per model |

Worker → ml-service calls carry `INTERNAL_SERVICE_TOKEN` (header `X-Internal-Service-Token`); unset, ml-service refuses them.
`unsure` labels are excluded from precision. Labels sit in `alert_labels`, not on the `alerts`
hypertable ([data-and-storage.md](data-and-storage.md)).

## Failure handling

| Cause | Handling |
|---|---|
| Bad reading (not JSON; missing `company_id`, `sensor_id` or numeric `value`; non-UUID id; unparseable time) | Dead-lettered to `KAFKA_TOPIC_DLQ` with the error; batch commits |
| Dependency down (database, pool, network) | Never dead-lettered; batch rewound to the last commit and retried with backoff `KAFKA_RETRY_BACKOFF_SECONDS` doubling to `KAFKA_MAX_BACKOFF_SECONDS` |
| Other error | Retried `KAFKA_MAX_RETRIES` times, then dead-lettered |
| Commit lost to a rebalance | Rewound; batch redelivered |
| Other Kafka error, or any worker loop ending | Process exits non-zero and is restarted |
| Consumer stalled | `healthcheck.py` fails once the heartbeat file is older than `HEARTBEAT_MAX_AGE_SECONDS` (compose healthcheck, chart exec liveness probe) |

## Deduplication

| Mechanism | Detail |
|---|---|
| Cooldown key | `(company_id, rule_id, sensor_id, equipment_id)`; retrain recommendations keyed by model |
| Cooldown window | `ALERT_COOLDOWN_SECONDS` around the reading's event time, checked in the database |
| Concurrent writers | `pg_advisory_xact_lock` on the key before the check |
| Exact replay | `alert_id` derived from key and event time; `ON CONFLICT DO NOTHING` |
| ML event time | Newest reading in the snapshot |
| In-memory cache | `ALERT_DEDUP_CACHE_MAX` entries in front of the query |

Code: `services/alert_service/worker/repository.py`.

## ML snapshot

| Item | Value |
|---|---|
| Store | Redis, `REDIS_URL` (required) |
| Key | `features:{company_id}:{equipment_id}:{sensor_name}` |
| Staleness | A sensor older than `FEATURE_MAX_AGE_SECONDS` by event time is missing; an incomplete snapshot is not scored |
| Degraded score | When ml-service reports `degraded`, the alert message names the neutralised features |

Code: `services/alert_service/worker/feature_store.py`.

## Settings (`services/alert_service/config.py`)

| Name | Default | Meaning |
|---|---|---|
| `KAFKA_TOPIC_DLQ` | `<KAFKA_TOPIC_SENSOR_DATA>.dlq` | Dead-letter topic |
| `KAFKA_MAX_RETRIES` | 3 | Retries before dead-letter |
| `KAFKA_RETRY_BACKOFF_SECONDS` / `KAFKA_MAX_BACKOFF_SECONDS` | 1.0 / 30 | Dependency-down backoff |
| `HEARTBEAT_MAX_AGE_SECONDS` | 120 | Liveness threshold |
| `ALERT_COOLDOWN_SECONDS` | 300 | Dedup window |
| `ALERT_DEDUP_CACHE_MAX` | 10000 | Dedup cache size |
| `FEATURE_MAX_AGE_SECONDS` | 300 | Snapshot staleness |
| `RULE_RELOAD_INTERVAL` | 60 | Rule reload, seconds |
| `ML_SERVICE_URL` | `http://ml-service-api:8002` | ml-service |
| `DRIFT_EVAL_INTERVAL` / `DRIFT_WINDOW_MINUTES` / `DRIFT_SHARE_THRESHOLD` | 3600 / 1440 / 0.5 | Drift evaluation |
| `RETRAIN_RECO_ENABLED` | true | Retrain recommendation on/off |
| `RETRAIN_PRECISION_FLOOR` / `RETRAIN_MIN_LABELS` / `RETRAIN_MIN_DRIFT_ALERTS` | 0.6 / 10 / 2 | Recommendation conditions |
| `RETRAIN_PRECISION_WINDOW_DAYS` / `RETRAIN_DRIFT_LOOKBACK_HOURS` | 30 / 72 | Recommendation windows |
| `RETRAIN_RECO_COOLDOWN_SECONDS` / `RETRAIN_RECO_SEVERITY` | 86400 / `high` | Recommendation cooldown, severity |
