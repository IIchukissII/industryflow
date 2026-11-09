<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Alerting

The manual of [ADR-0031](../../ADR/ADR-0031-the-api-gateway-as-the-single-owner-of-the-alerting-http-surface.md), [ADR-0021](../../ADR/ADR-0021-model-drift-monitoring-the-statistical-alert-lane.md), [ADR-0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md): the alert API, the detection worker, rule types, deduplication, drift rules and operator labels.

## ADR-0031 dec 1

The gateway serves rules and history.

| Code | Role |
|---|---|
| `services/api_gateway/routers/alert_rules.py` | Alert rules |
| `services/api_gateway/routers/alerts_history.py` | Alert history, acknowledgement, labels, precision |

### Components

| Name | Role | Port | Config |
|---|---|---|---|
| `api-gateway` | Rule CRUD, alert history, acknowledge, labels, precision | 8000 | `services/api_gateway/routers/alert_rules.py`, `alerts_history.py` |

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/alert-rules`, `/api/alert-rules/{rule_id}` | session | List (with `sensor_name`, `sensor_display_name`, `equipment_name`, `model_name`, `model_version`; NULL for a deleted target), read |
| POST | `/api/alert-rules` | `engineer`, `admin` | Create |
| PUT, DELETE | `/api/alert-rules/{rule_id}` | `engineer`, `admin` | Replace, delete |
| PATCH | `/api/alert-rules/{rule_id}/detection-mode` | `engineer`, `admin` | Change detection type |
| GET | `/api/alerts`, `/api/alerts/unacknowledged`, `/api/alerts/critical` | session | History, filtered history; each alert with `sensor_name`, `sensor_display_name`, `equipment_name`, `rule_name`, `detection_type`, `degraded`, `neutralized_features` (both `null` on an alert from before they were recorded) |
| PATCH | `/api/alerts/{alert_id}/acknowledge` | `engineer`, `admin` | Acknowledge |
| PATCH | `/api/alerts/{alert_id}/label` | `engineer`, `admin` | `true_positive` / `false_positive` / `unsure`; the label copies the alert's `degraded` |
| GET | `/api/alerts/label-metrics` | session | Precision = TP/(TP+FP) and false-positive rate per bucket and summary; optional model or rule scope; which labels each summary counts: [ADR-0022 dec 1-3](#adr-0022-dec-1-3) |

### Rule fields

| Field | Values |
|---|---|
| `detection_type` | `threshold` (default), `ml`, `statistical`; `ml` and `statistical` require `model_id` |
| `condition` | `greater_than`, `less_than`, `equals`, `not_equals`, `between`, `outside_range` |
| `threshold`, `threshold_min`, `threshold_max` | Threshold bounds; a `statistical` threshold is a share in 0–1 |
| `anomaly_threshold` | `ml` score threshold, 0–1, default 0.85 |
| `severity` | `low`, `medium` (default), `high`, `critical` |
| `company_id` | Ignored; the tenant is the caller's |

```bash
curl -X POST https://<host>/api/alert-rules -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "High temperature", "sensor_pattern": "temp_*", "detection_type": "threshold",
       "condition": "greater_than", "threshold": 80.0, "severity": "critical"}'
```

## ADR-0031 dec 2

The detection worker serves no HTTP API.

| Code | Role |
|---|---|
| `services/alert_service/worker/main.py::AlertService` | Detection worker: consumption, rule reload, ML and drift schedules |
| `services/alert_service/worker/kafka_consumer.py` | Kafka consumption, dead-lettering, rewind |
| `services/alert_service/worker/rules_engine.py::RulesEngine` | Rule evaluation, alert emission |
| `services/alert_service/worker/repository.py` | Alert writes, deduplication |
| `services/alert_service/worker/feature_store.py` | ML snapshot store |
| `services/alert_service/worker/healthcheck.py` | Liveness from the heartbeat file |

### Components

| Name | Role | Port | Config |
|---|---|---|---|
| `alert-service-detector` | Kafka consumer on `KAFKA_TOPIC_SENSOR_DATA`; evaluates rules, writes `alerts`; no HTTP API | 9108, `industryflow_build_info` only ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 8) | `services/alert_service/config.py`, `services/alert_service/worker/` |
| `ml-service-api` | Scores `ml` rules, computes drift ([ML and features](ml-and-features.md)) | 8002 | `services/ml_service/` |

### Rule types (`alert_rules.detection_type`)

| Type | Evaluated | How |
|---|---|---|
| `threshold` | Per reading | Value over / under `threshold`, or outside `threshold_min`–`threshold_max` |
| `ml` | Per reading | Worker calls `POST /api/inference/predict`; alert when the score crosses `anomaly_threshold` |
| `statistical` | On a schedule | See [ADR-0021 dec 2, 3, 5](#adr-0021-dec-2-3-5) |

Worker → `ml-service-api` calls carry the internal service token
([authentication](../operations/authentication.md#configuration)).

### Failure handling

| Cause | Handling |
|---|---|
| Bad reading (not JSON; missing `company_id`, `sensor_id` or numeric `value`; non-UUID id; unparseable time) | Dead-lettered to `KAFKA_TOPIC_DLQ` with the reason ([dead letters](../operations/dead-letters.md)); batch commits |
| Dependency down (database, pool, network) | Never dead-lettered; batch rewound to the last commit and retried with backoff `KAFKA_RETRY_BACKOFF_SECONDS` doubling to `KAFKA_MAX_BACKOFF_SECONDS` |
| Other error | Retried `KAFKA_MAX_RETRIES` times, then dead-lettered |
| Commit lost to a rebalance | Rewound; batch redelivered |
| Other Kafka error, or any worker loop ending | Process exits non-zero and is restarted |
| Consumer stalled | `healthcheck.py` fails once the heartbeat file is older than `HEARTBEAT_MAX_AGE_SECONDS` (compose healthcheck, chart exec liveness probe) |

### Deduplication

| Mechanism | Detail |
|---|---|
| Cooldown key | `(company_id, rule_id, sensor_id, equipment_id)`; retrain recommendations keyed by model |
| Open recommendation | A retrain recommendation is also suppressed while an earlier one for its model is unacknowledged |
| Cooldown window | `ALERT_COOLDOWN_SECONDS` around the reading's event time, checked in the database |
| Concurrent writers | `pg_advisory_xact_lock` on the key before the check |
| Exact replay | `alert_id` derived from key and event time; `ON CONFLICT DO NOTHING` |
| ML event time | Newest reading in the snapshot |
| In-memory cache | `ALERT_DEDUP_CACHE_MAX` entries in front of the query |
| Hypertable chunks | The window check reaches across a chunk boundary; the primary key is per chunk and includes the event time, so an exact replay meets its row in the same chunk |

| Code | Role |
|---|---|
| `services/alert_service/worker/repository.py` | Cooldown check, advisory lock, idempotent insert |
| `infrastructure/timescaledb/tests/test_alert_dedup_hypertable.py` | Proof on the alerts hypertable: window, replay, out-of-order and scope cases at a chunk boundary; concurrent writers across existing and new chunks; the engine tests' `FakeAlertStore` held to the same outcomes |
| `infrastructure/timescaledb/tests/test_alert_dedup_live.py` | Proof on the live store |

### ML snapshot

| Item | Value |
|---|---|
| Store | Redis, `REDIS_URL` (required) |
| Key | `features:{company_id}:{equipment_id}:{sensor_name}` |
| Staleness | A sensor older than `FEATURE_MAX_AGE_SECONDS` by event time is missing; an incomplete snapshot is not scored |
| Degraded score | When `ml-service-api` reports `degraded`, the alert records `degraded: true` and `neutralized_features`; the retrain recommendation leaves labels on such alerts out |

| Code | Role |
|---|---|
| `services/alert_service/worker/feature_store.py::FeatureStore` | Latest-value snapshot |

### Settings (`services/alert_service/config.py`)

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
| `ML_SERVICE_URL` | `http://ml-service-api:8002` | `ml-service-api` base URL |

## ADR-0031 dec 3

Operator labels and label precision, the capabilities added after rules and history, are routes of the gateway's alert-history router: `PATCH /api/alerts/{alert_id}/label` and `GET /api/alerts/label-metrics`.

| Code | Role |
|---|---|
| `services/api_gateway/routers/alerts_history.py::label_alert` | Label route |
| `services/api_gateway/routers/alerts_history.py` | Label-metrics route |

## ADR-0021 dec 2, 3, 5

A `statistical` rule binds a model and a drifted-feature share threshold. The worker evaluates it on a schedule, apart from the per-reading path; `ml-service-api` computes the drift ([ML and features](ml-and-features.md#adr-0021-dec-1-3-5)).

| Code | Role |
|---|---|
| `services/alert_service/worker/main.py::_periodic_drift_evaluation` | Drift schedule |
| `services/alert_service/worker/rules_engine.py::evaluate_drift_rules` | Statistical rule evaluation, threshold applied |
| `services/alert_service/worker/rules_engine.py::_evaluate_statistical` | No-op per reading |

| Type | Evaluated | How |
|---|---|---|
| `statistical` | Every `DRIFT_EVAL_INTERVAL` over `DRIFT_WINDOW_MINUTES` | Worker calls `POST /api/drift/evaluate` for the rule's `model_id`; alert when the drifted-feature share crosses the rule threshold; no-op per reading |

| Setting | Default | Meaning |
|---|---|---|
| `DRIFT_EVAL_INTERVAL` / `DRIFT_WINDOW_MINUTES` / `DRIFT_SHARE_THRESHOLD` | 3600 / 1440 / 0.5 | Drift evaluation |

## ADR-0022 dec 1-3

Labels are written through the alert history API into a per-tenant table; precision is derived on read. `unsure` labels are excluded from precision. Labels sit in `alert_labels`, not on the `alerts`
hypertable ([data and storage](data-and-storage.md)).

`GET /api/alerts/label-metrics` summaries:

| Field | Labels counted |
|---|---|
| `overall`, `series` | Not degraded (`null`, from before degradation was recorded, counts with them); with `model_id`, only on `ml` alerts (the model's own score) |
| `degraded` | On degraded alerts; with `model_id`, only on `ml` alerts |
| `monitoring` | With `model_id`: on the model's `statistical` alerts (drift, retrain recommendations); otherwise empty |

| Code | Role |
|---|---|
| `services/api_gateway/routers/alerts_history.py` | Labels and label metrics; `LABEL_ALERT_SELECT`, `LABEL_UPSERT`, `LABEL_METRICS_SELECT` |
| `services/frontend/src/pages/AlertHistory.jsx` | Alerts page: acknowledge, label (Real / False / Unsure); label precision over 30 days in the 24-hour summary |
| `infrastructure/timescaledb/init-scripts/35-degraded-alerts.sql` | `alerts.degraded`, `alerts.neutralized_features`, `alert_labels.degraded` |
| `infrastructure/timescaledb/tests/test_degraded_alerts.py` | Proof: stored, read, copied onto the label, counted apart (degraded, monitoring), on the real store |

## ADR-0022 dec 4, 5

After each drift pass the alert worker evaluates the retrain recommendation, with a per-model cooldown. It raises one when the model has at least `RETRAIN_MIN_DRIFT_ALERTS` drift alerts in the lookback and its precision is below the floor over at least `RETRAIN_MIN_LABELS` decided labels on its own `ml` alerts that were not degraded. The recommendation is `high`. While it is unacknowledged the model is marked on the models page and no second one is raised. Invalid settings stop the worker at start.

| Code | Role |
|---|---|
| `services/alert_service/worker/main.py` | Recommendation after the drift pass |
| `services/alert_service/worker/rules_engine.py` | Recommendation evaluator |
| `services/alert_service/worker/repository.py::MODEL_LABEL_COUNTS` | Decided labels on the model's `ml` alerts, those on degraded alerts left out |
| `services/alert_service/worker/repository.py::_RETRAIN_OPEN` | No second recommendation while one is open |
| `services/alert_service/config.py::retrain_settings_invalid` | Settings refused at start |
| `services/frontend/src/pages/MLModels.jsx` | Model marked while a recommendation is unacknowledged |

| Type | Evaluated | How |
|---|---|---|
| Retrain recommendation | Scheduled, beside drift | `statistical` alert with `condition='retrain_recommended'` when a model shows sustained drift and precision below `RETRAIN_PRECISION_FLOOR`; cooldown per model |

| Setting | Default | Meaning |
|---|---|---|
| `RETRAIN_RECO_ENABLED` | true | Retrain recommendation on/off |
| `RETRAIN_PRECISION_FLOOR` | 0.6, in (0, 1] | Precision below this is decayed |
| `RETRAIN_MIN_LABELS` / `RETRAIN_MIN_DRIFT_ALERTS` | 10 / 2, each at least 1 | Recommendation conditions |
| `RETRAIN_PRECISION_WINDOW_DAYS` / `RETRAIN_DRIFT_LOOKBACK_HOURS` | 30 / 72 | Recommendation windows |
| `RETRAIN_RECO_COOLDOWN_SECONDS` | 86400 | Recommendation cooldown, from the last recommendation |

## ADR-0022 dec 6

The label write runs on the caller's tenant schema; `labeled_by` records the labelling user's id and `labeled_at` the time, both rewritten when a label changes. Alert lists return them with the label.

| Code | Role |
|---|---|
| `services/api_gateway/routers/alerts_history.py::label_alert` | Writer-only label write on the tenant session |
| `services/api_gateway/routers/alerts_history.py::LABEL_UPSERT` | `labeled_by`, `labeled_at` |

Links: [ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md) (at-least-once consumption, dead letters), [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) (tenant resolution), [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md) (Redis latest-value snapshot for ML rules), [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md) (degraded scores under the stateful-feature kill-switch), [ML and features](ml-and-features.md).
