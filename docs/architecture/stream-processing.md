<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Stream processing

The manual of [ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md), [ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md), [ADR-0029](../../ADR/ADR-0029-spark-major-upgrades.md), [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md): the path from a device reading to stored time-series, the stream's bounds on a suspended tenant, the live-value read path, and Spark upgrades.

## ADR-0005 dec 1

Every consumer of `sensor-data-raw` advances its position only after its batch is handled.

| Consumer | Position | Advanced |
|---|---|---|
| `spark-streaming`, `spark-aggregations` | Offsets in the query's checkpoint | When the micro-batch completes |
| `alert-service-detector` | Committed offsets of its consumer group (`enable_auto_commit=False`) | After the whole batch is processed or dead-lettered |

| Code | Role |
|---|---|
| `services/alert_service/worker/kafka_consumer.py::AlertKafkaConsumer` | Alert worker consumption loop |
| `services/spark_jobs/kafka_to_timescaledb.py::process_batch` | Measurements micro-batch |
| `services/spark_jobs/kafka_aggregations.py::write_aggregations_to_db` | Aggregation micro-batch |
| `services/alert_service/tests/test_kafka_consumer_integration.py::test_a_worker_killed_before_its_commit_loses_nothing` | Proof: alert worker |
| `services/spark_jobs/tests/test_streaming_integration.py::test_a_killed_job_resumes_from_its_checkpoint_without_loss_or_duplicate` | Proof: measurements job |

### Write path

```
device ──mTLS──▶ ingestion-edge ─▶ ingestion-service ─▶ Kafka sensor-data-raw ─┬▶ spark-streaming    ─▶ tenant_<uuid>.sensor_measurements
                                                                                └▶ spark-aggregations ─▶ tenant_<uuid>.sensor_aggregations_{1min,5min,1hour}
```

| Name | Role | Port | Config |
|---|---|---|---|
| `ingestion-service` | Validates the reading, stamps the verified `company_id`, produces to Kafka; rejects a timestamp more than 300 s ahead (HTTP 422) and a suspended tenant (HTTP 403, from `tenant-status`; ADR-0033 dec 3); no database access (ADR-0002 dec 5, 8) | 8003 | `services/ingestion_service/main.py` |
| `spark-streaming` | Upserts raw readings (ADR-0006 dec 3); reads from `earliest` (ADR-0005 dec 4) | 4040 (metrics), 9108 (revision) | `services/spark_jobs/kafka_to_timescaledb.py` |
| `spark-aggregations` | Windows readings into 1 min / 5 min / 1 h aggregates (ADR-0006 dec 1, 2); reads from `latest` (ADR-0005 dec 4) | 4040 (metrics), 9108 (revision) | `services/spark_jobs/kafka_aggregations.py`, `aggregation_windows.py` |
| Reading guard | One definition of an unwritable reading, shared by both jobs (ADR-0006 dec 4) | | `services/spark_jobs/reading_guard.py` |
| Window contract | Window widths and allowed lateness (ADR-0006 dec 7) | | `libs/industryflow_contracts/industryflow_contracts/windows.py` |
| `spark-master`, `spark-worker` | Standalone cluster (Compose); executors reach the object-store checkpoint (ADR-0006 dec 5) | 7077, 8080 | `services/spark_jobs/Dockerfile.worker` |

### Kafka topics

| Topic | Producer | Consumers | Setting |
|---|---|---|---|
| `sensor-data-raw` | `ingestion-service` | `spark-streaming`, `spark-aggregations`, `alert-service-detector` | `KAFKA_TOPIC_SENSOR_DATA` |
| `sensor-data-raw.measurements.dlq` | `spark-streaming` | Operators | `KAFKA_TOPIC_MEASUREMENTS_DLQ` |
| `<KAFKA_TOPIC_SENSOR_DATA>.dlq` | `alert-service-detector` | Operators | `KAFKA_TOPIC_DLQ` ([alerting](alerting.md)) |
| `tenant-status` | `alert-service-detector` | `ingestion-service` | `KAFKA_TOPIC_TENANT_STATUS` ([ADR-0033 dec 3](#adr-0033-dec-3)) |

Tenant routing by the verified `company_id`: [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) ([data and storage](data-and-storage.md#adr-0003-dec-1-3)).

## ADR-0005 dec 2-4, 6

The alert worker commits an offset only after its write is durable, retries a failure without advancing, dead-letters a message no retry can process, and suppresses a repeated alert within its cooldown.

| Code | Role |
|---|---|
| `services/alert_service/worker/kafka_consumer.py` | Manual commit, retry, dead-letter |
| `services/alert_service/config.py` | Retry, dead-letter and cooldown settings |
| `services/alert_service/tests/test_kafka_consumer_integration.py` | Decisions 2-4 against the platform's broker |

A letter on a `*.dlq` topic fires `IndustryFlowDeadLetteredReadings` on Compose and Kubernetes
([monitoring](../operations/monitoring.md#alerts)); inspection and replay:
[dead-letter handling](../operations/dead-letters.md).

## ADR-0005 dec 5

Every producer sets `acks=all`; the client retries within its delivery timeout. An ingestion send that still fails answers 503 and is not counted as sent.

| Code | Role |
|---|---|
| `services/ingestion_service/kafka_producer.py::AsyncKafkaProducerSingleton` | Ingestion producer |
| `services/spark_jobs/reading_guard.py::publish_dead_letters` | Dead-letter writer |
| `services/alert_service/worker/kafka_consumer.py` | Alert worker producer (`acks='all'`, idempotence on) |
| `services/ingestion_service/tests/test_kafka_producer_integration.py` | Proof: a broker outage answers 503, the next reading is delivered once |

## ADR-0006 dec 1, 2

Each granularity's query declares `withWatermark("time", <allowed lateness>)` and writes in append mode: a window's row is emitted once, when the watermark passes its end. `UNIQUE (time, sensor_id, equipment_id)` holds one row per window and series.

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_aggregations.py::aggregate_readings` | Watermark and window |
| `services/spark_jobs/kafka_aggregations.py::create_aggregation_stream` | Append-mode query |
| `services/spark_jobs/aggregation_windows.py::watermark_delay` | Allowed lateness from the window contract |
| `services/spark_jobs/tests/test_aggregations_integration.py::test_a_window_is_written_once_the_watermark_passes_it_and_a_late_reading_changes_nothing` | Proof |

## ADR-0006 dec 3

| Table | Write |
|---|---|
| `sensor_measurements` | `ON CONFLICT (time, sensor_id) DO NOTHING` |
| `sensor_aggregations_*` | `ON CONFLICT (time, sensor_id, equipment_id) DO UPDATE` |

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_to_timescaledb.py::_make_measurement_upsert` | First write wins |
| `services/spark_jobs/kafka_aggregations.py::_upsert_values` | Replace on conflict |
| `services/spark_jobs/tests/test_streaming_integration.py::test_a_killed_job_resumes_from_its_checkpoint_without_loss_or_duplicate` | Proof: a redelivered reading stays single |

## ADR-0006 dec 4

A database or Kafka error re-raises and fails the batch.

| Code | Role |
|---|---|
| `services/spark_jobs/reading_guard.py::rejection_reason` | Stateless rule shared by both jobs |
| `services/spark_jobs/reading_guard.py::classify_references` | Tenant, sensor and equipment rule shared by both jobs |
| `services/spark_jobs/kafka_to_timescaledb.py::process_batch` | Dead-letters first, then writes; re-raises a write failure |
| `services/spark_jobs/kafka_aggregations.py::write_aggregations_to_db` | Excludes refused keys; re-raises any other failure |
| `services/spark_jobs/tests/test_reading_guard.py` | Proof: which readings are writable |
| `services/spark_jobs/tests/test_streaming_batch.py` | Proof: batch routing |
| `services/spark_jobs/tests/test_aggregations.py::test_a_future_timestamp_does_not_close_live_windows` | Proof: rejected readings do not move the watermark |

### Unwritable readings

A reading that is malformed, lacks a required field, carries a non-UUID id, is stamped more than
300 s ahead (`MAX_FUTURE_SKEW_SECONDS`), names a tenant the registry does not hold or whose schema is
missing (`unknown_tenant`), names a suspended tenant (`suspended_tenant`,
[ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) dec 3), or names a sensor or equipment that does
not exist:

| Job | Handling |
|---|---|
| spark-streaming | Published to `sensor-data-raw.measurements.dlq` with the reason and its topic/partition/offset, before the batch's writes; the rest of the batch is written |
| spark-aggregations | Stateless rules applied before the watermark; the unknown/suspended tenant and unknown sensor/equipment rules applied at write time on the aggregate key; not published again |

## ADR-0006 dec 5

An `s3a://` `CHECKPOINT_LOCATION` enables S3A.

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_to_timescaledb.py::configure_s3a` | S3A checkpoint (measurements) |
| `services/spark_jobs/kafka_aggregations.py::configure_s3a` | S3A checkpoint (aggregations) |
| `services/spark_jobs/tests/test_aggregations_integration.py::test_a_killed_job_resumes_its_open_window_and_counts_each_reading_once` | Proof: window state resumes from the object store |

### Checkpoints

| Deployment | `CHECKPOINT_LOCATION` | Storage |
|---|---|---|
| Compose | `s3a://spark-checkpoints` (`SPARK_CHECKPOINT_LOCATION`) | MinIO via S3A, shared by the driver and every `spark-worker` |
| Helm | `/opt/spark/checkpoints` | ReadWriteOnce PVC per job; Spark in `local[*]` mode |
| Helm, on the object store | `s3a://…` | [Helm chart](../../deploy/helm/industryflow/README.md#spark-checkpoint-on-the-object-store) |

| Query | Path under `CHECKPOINT_LOCATION` |
|---|---|
| Measurements | `sensor_measurements` |
| Aggregations | `sensor_aggregations_<granularity>-p<N>`, `N` = state partitions (`SPARK_AGG_STATE_PARTITIONS`, default 2) |

A local path disables S3A. The S3A jars (`hadoop-aws`, AWS SDK v2 `bundle`) are baked into all three
Spark images; the Kafka connector is `spark-sql-kafka-0-10_2.13`.

## ADR-0006 dec 6

The measurements job waits on `awaitTermination()`, the aggregation job on `awaitAnyTermination()`; a failed query raises and the process exits.

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_to_timescaledb.py::main` | Measurements job |
| `services/spark_jobs/kafka_aggregations.py` | Aggregation job |
| `services/spark_jobs/tests/test_streaming_integration.py::test_no_offset_advances_while_the_database_is_down` | Proof: the job exits, no offset advances |
| `services/spark_jobs/tests/test_aggregations_integration.py::test_a_database_outage_fails_the_batch_and_the_window_is_written_on_restart` | Proof |

### Live-value read path

```
sensor_measurements ─▶ api-gateway cache_updater (2 s poll, last hour) ─▶ Redis sensor:latest:<sensor_id> (TTL 60 s) ─▶ WS /ws/sensors ─▶ UI
```

| Symptom | Cause | Fix |
|---|---|---|
| UI shows no live data; ingestion and Kafka healthy; gateway log `0 fresh sensor_measurements in the last hour … is spark-streaming (raw-measurements writer) running?` | `spark-streaming` stopped | Start `spark-streaming` |

Code: `services/api_gateway/messaging/cache_updater.py`, `services/api_gateway/routers/websocket.py`.

## ADR-0006 dec 7

Window width and allowed lateness are read from one shared definition by the stream and by every reader of the windows.

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/windows.py::WINDOWS` | The window contract |
| `tests/config_guards/test_tenant_contract_copies.py::test_no_service_or_script_builds_a_tenant_name_or_restates_the_window_contract` | Proof: no component restates it |

## ADR-0006 dec 8

The aggregation job sets `spark.sql.shuffle.partitions` from `SPARK_SQL_SHUFFLE_PARTITIONS` (Compose: `SPARK_AGG_STATE_PARTITIONS`, default 2) and names each query's checkpoint `<table>-p<N>`.

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_aggregations.py::state_partitions` | Partition count |
| `services/spark_jobs/kafka_aggregations.py::checkpoint_path` | Checkpoint keyed by the count |
| `services/spark_jobs/tests/test_aggregations.py::test_a_changed_partition_count_gets_its_own_checkpoint` | Proof: a changed count is a fresh checkpoint |
| `services/spark_jobs/tests/test_aggregations.py::test_the_job_resumes_a_checkpoint_written_by_the_deployed_aggregation` | Proof: the aggregation's state shape is the deployed one |

Changing `N` starts the aggregation queries on fresh checkpoints from the latest offsets
([ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) dec 8): each series' window
open at the switch is written from post-switch readings only. The previous `-p<M>` (or unsuffixed)
directories are no longer read; delete them once the new queries run.

The state shape is the aggregation's grouping and aggregate expressions in `aggregate_readings`. A
change to them fails `test_the_job_resumes_a_checkpoint_written_by_the_deployed_aggregation`; a
deliberate one is deployed as a reset ([ADR-0029 dec 3](#adr-0029-dec-3)), with the aggregation
checkpoints deleted while the job is stopped, and the test's deployed shape updated with it.

## ADR-0006 dec 9

The aggregation writes `stddev_value` as the sample standard deviation, and 0 for a window with `count_values` 1, set after the aggregation so the checkpointed state keeps its shape. Offline training derives it by the same rule.

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_aggregations.py::aggregate_readings` | Writer: zero spread for one reading |
| `services/ml_service/notebooks/utils/offline_baseline.py::add_window_features` | Training: the same rule |
| `infrastructure/timescaledb/init-scripts/36-window-spread-of-one-reading.sql` | Windows written before: set to 0 |
| `services/spark_jobs/tests/test_aggregations.py::test_a_window_of_one_reading_has_zero_spread` | Proof: the writer |
| `services/spark_jobs/tests/test_aggregations.py::test_the_job_resumes_a_checkpoint_written_by_the_deployed_aggregation` | Proof: the rule is applied after the aggregation and leaves the checkpointed state as it is |
| `infrastructure/timescaledb/tests/test_window_spread_migration.py` | Proof: the backfill, compressed chunks included, on the real store |

## ADR-0029 dec 1

A major upgrade relies on the idempotent sinks of [ADR-0006 dec 3](#adr-0006-dec-3): a re-read of the source re-upserts the same rows.

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_to_timescaledb.py::_make_measurement_upsert` | Raw measurements, first write wins |
| `services/spark_jobs/kafka_aggregations.py::_upsert_values` | Aggregates, replace on conflict |

## ADR-0029 dec 2

Whether the checkpoint resumes or must be reset is decided per boundary by running it on the box. 3.5 → 4.1 resumes. The dependency bot proposes no Spark minor or major update; each is a deliberate upgrade.

| Code | Role |
|---|---|
| `.github/dependabot.yml` | Ignores `apache/spark` minor and major updates |
| `scripts/box-deploy.sh` | Box deploy: stops the Spark services for the build and starts them on the existing checkpoint |

## ADR-0029 dec 3

A boundary that does not resume is reset: the checkpoint is deleted and both jobs restart.

Reset cost: measurements re-read from `earliest` within Kafka retention; aggregations restart from
`latest`, so each window open at the reset instant is written from the readings after it. Pausing
producers until the widest window's watermark passes closes those windows before the reset.

### Spark major upgrade

| Step | Resume | Reset |
|---|---|---|
| 1 | Build the new images | Same |
| 2 | `docker compose stop spark-streaming spark-aggregations` | Same |
| 3 | — | Delete the checkpoint: `mc rm -r --force <alias>/spark-checkpoints`, or the contents of `/opt/spark/checkpoints` |
| 4 | `docker compose up -d --build spark-master spark-worker spark-streaming spark-aggregations` | Same |

| Check after the upgrade | Expected |
|---|---|
| Driver logs | Micro-batches; `spark-submit` processes up |
| `tenant_<uuid>.sensor_measurements` | Row count rising |
| `tenant_<uuid>.sensor_aggregations_{1min,5min,1hour}` | Fresh rows |

| Symptom | Cause | Fix |
|---|---|---|
| `NoClassDefFoundError` / `ClassNotFoundException` at submit | Kafka connector or S3A jars mismatched to the Spark/Hadoop version | Align the jar versions in the Spark images with the Spark/Hadoop version |

## ADR-0029 dec 4

The per-image smoke asserts each Spark image is Spark 4.1 / Scala 2.13 with the S3A jars baked, and that each job module imports through the `--py-files` its image ships. It does not run a batch; the box run does.

| Code | Role |
|---|---|
| `.github/workflows/images.yml` | Per-image smoke of `spark-streaming`, `spark-aggregations`, `spark-worker` |
| `scripts/check_spark_version_parity.py` | Every place that writes the Spark version names the images' version |

## ADR-0033 dec 3

| Point | Bound on a suspended tenant |
|---|---|
| `alert-service-detector` | Publishes every registered tenant's status on `tenant-status` every `TENANT_STATUS_PUBLISH_SECONDS` (10 s); evaluates no rule of a suspended tenant |
| `ingestion-service` | Follows `tenant-status` outside any group, from `latest`; refuses a suspended tenant's reading (HTTP 403); a status unheard for 60 s is forgotten |
| `spark-streaming`, `spark-aggregations` | Refuse a reading whose tenant the registry does not hold as active (`suspended_tenant`, [unwritable readings](#unwritable-readings)) |
| `IndustryFlowTenantStatusStalled` | Fires when a `tenant-status` partition that carried statuses within the hour carries none for 5 m |

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/tenant_status.py` | The status message, `PUBLISH_INTERVAL_SECONDS`, `STALE_AFTER_SECONDS` |
| `services/alert_service/worker/tenant_status_publisher.py` | Publisher |
| `services/ingestion_service/tenant_status.py::TenantStatus` | Ingestion's view of suspended tenants |
| `services/ingestion_service/main.py::tenant_status_consumer` | Ingestion's consumer of the stream |
| `services/spark_jobs/reading_guard.py::TENANT_STATUS_SELECT` | Writers read the tenant's status from the registry |
| `infrastructure/prometheus/rules/platform.yml` | `IndustryFlowTenantStatusStalled` |

Links: [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md) (aggregation windows as a feature contract), [dead-letter handling](../operations/dead-letters.md).
