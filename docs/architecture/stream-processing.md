<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Stream processing

The path from a device reading to stored time-series, the live-value read path, and Spark upgrades.

| Decision | ADR |
|---|---|
| At-least-once consumption, manual commits, dead-letter | [ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md) |
| Watermark + append windows, idempotent upserts, durable checkpoint | [ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) |
| Aggregation windows as a feature contract | [ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) dec 7, [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md) |
| Tenant routing by verified `company_id` | [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
| Checkpoint across a Spark major upgrade | [ADR-0029](../../ADR/ADR-0029-spark-major-upgrade-checkpoint-cutover.md) |

## Write path

```
device ──mTLS──▶ ingestion-edge ─▶ ingestion-service ─▶ Kafka sensor-data-raw ─┬▶ spark-streaming    ─▶ tenant_<uuid>.sensor_measurements
                                                                                └▶ spark-aggregations ─▶ tenant_<uuid>.sensor_aggregations_{1min,5min,1hour}
```

| Name | Role | Port | Config |
|---|---|---|---|
| `ingestion-service` | Validates the reading, stamps the verified `company_id`, produces to Kafka; rejects a timestamp more than 300 s ahead (HTTP 422) and a suspended tenant (HTTP 403, from `tenant-status`); no database access | 8003 | `services/ingestion_service/` |
| `spark-streaming` | Upserts raw readings; reads from `earliest` | 4040 (metrics) | `services/spark_jobs/kafka_to_timescaledb.py` |
| `spark-aggregations` | Windows readings into 1 min / 5 min / 1 h aggregates; reads from `latest` | 4040 (metrics) | `services/spark_jobs/kafka_aggregations.py`, `aggregation_windows.py` |
| Reading guard | One definition of an unwritable reading, shared by both jobs | | `services/spark_jobs/reading_guard.py` |
| Window contract | Window widths and allowed lateness | | `libs/industryflow_contracts/industryflow_contracts/windows.py` |
| `spark-master`, `spark-worker` | Standalone cluster (Compose) | 7077, 8080 | `services/spark_jobs/Dockerfile.worker` |

## Kafka topics

| Topic | Producer | Consumers | Setting |
|---|---|---|---|
| `sensor-data-raw` | `ingestion-service` | `spark-streaming`, `spark-aggregations`, `alert-service-detector` | `KAFKA_TOPIC_SENSOR_DATA` |
| `sensor-data-raw.measurements.dlq` | `spark-streaming` | Operators | `KAFKA_TOPIC_MEASUREMENTS_DLQ` |
| `<KAFKA_TOPIC_SENSOR_DATA>.dlq` | `alert-service-detector` | Operators | `KAFKA_TOPIC_DLQ` ([alerting](alerting.md)) |
| `tenant-status` | `alert-service-detector`: every registered tenant's status every `TENANT_STATUS_PUBLISH_SECONDS` (10 s) | `ingestion-service`, outside any group, from `latest`; a status unheard for 60 s is forgotten | `KAFKA_TOPIC_TENANT_STATUS` ([ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) dec 3; message in `libs/industryflow_contracts/industryflow_contracts/tenant_status.py`) |

A letter on a `*.dlq` topic fires `IndustryFlowDeadLetteredReadings` on Compose and Kubernetes
([monitoring](../operations/monitoring.md#alerts)); inspection and replay:
[dead-letter handling](../operations/dead-letters.md).

## Unwritable readings

A reading that is malformed, lacks a required field, carries a non-UUID id, is stamped more than
300 s ahead (`MAX_FUTURE_SKEW_SECONDS`), names a tenant the registry does not hold or whose schema is
missing (`unknown_tenant`), names a suspended tenant (`suspended_tenant`,
[ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) dec 3), or names a sensor or equipment that does
not exist:

| Job | Handling |
|---|---|
| spark-streaming | Published to `sensor-data-raw.measurements.dlq` with the reason and its topic/partition/offset, before the batch's writes; the rest of the batch is written |
| spark-aggregations | Stateless rules applied before the watermark; the unknown/suspended tenant and unknown sensor/equipment rules applied at write time on the aggregate key; not published again |

A failure of the database or Kafka fails the batch.

## Checkpoints

| Deployment | `CHECKPOINT_LOCATION` | Storage |
|---|---|---|
| Compose | `s3a://spark-checkpoints` (`SPARK_CHECKPOINT_LOCATION`) | MinIO via S3A, shared by the driver and every `spark-worker` |
| Helm | `/opt/spark/checkpoints` | ReadWriteOnce PVC per job; Spark in `local[*]` mode |
| Helm, on the object store | `s3a://…` | [Helm chart](../../deploy/helm/industryflow/README.md#spark-checkpoint-on-the-object-store) |

| Query | Path under `CHECKPOINT_LOCATION` |
|---|---|
| Measurements | `sensor_measurements` |
| Aggregations | `sensor_aggregations_<granularity>-p<N>`, `N` = state partitions (`SPARK_AGG_STATE_PARTITIONS`, default 2) |

Changing `N` starts the aggregation queries on fresh checkpoints from the latest offsets
([ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) dec 8): each series' window
open at the switch is written from post-switch readings only. The previous `-p<M>` (or unsuffixed)
directories are no longer read; delete them once the new queries run.

A local path disables S3A. The S3A jars (`hadoop-aws`, AWS SDK v2 `bundle`) are baked into all three
Spark images; the Kafka connector is `spark-sql-kafka-0-10_2.13`.

## Live-value read path

```
sensor_measurements ─▶ api-gateway cache_updater (2 s poll, last hour) ─▶ Redis sensor:latest:<sensor_id> (TTL 60 s) ─▶ WS /ws/sensors ─▶ UI
```

| Symptom | Cause | Fix |
|---|---|---|
| UI shows no live data; ingestion and Kafka healthy; gateway log `0 fresh sensor_measurements in the last hour … is spark-streaming (raw-measurements writer) running?` | `spark-streaming` stopped | Start `spark-streaming` |

Code: `services/api_gateway/messaging/cache_updater.py`, `services/api_gateway/routers/websocket.py`.

## Spark major upgrade

Whether the checkpoint resumes or must be reset is decided per boundary by running it
([ADR-0029](../../ADR/ADR-0029-spark-major-upgrade-checkpoint-cutover.md)). 3.5 → 4.1 resumes.

| Step | Resume | Reset |
|---|---|---|
| 1 | Build the new images | Same |
| 2 | `docker compose stop spark-streaming spark-aggregations` | Same |
| 3 | — | Delete the checkpoint: `mc rm -r --force <alias>/spark-checkpoints`, or the contents of `/opt/spark/checkpoints` |
| 4 | `docker compose up -d --build spark-master spark-worker spark-streaming spark-aggregations` | Same |

Reset cost: measurements re-read from `earliest` within Kafka retention; aggregations lose the windows
open at the reset instant. Pausing producers until the widest window's watermark passes avoids that
loss.

| Check after the upgrade | Expected |
|---|---|
| Driver logs | Micro-batches; `spark-submit` processes up |
| `tenant_<uuid>.sensor_measurements` | Row count rising |
| `tenant_<uuid>.sensor_aggregations_{1min,5min,1hour}` | Fresh rows |

| Symptom | Cause | Fix |
|---|---|---|
| `NoClassDefFoundError` / `ClassNotFoundException` at submit | Kafka connector or S3A jars mismatched to the Spark/Hadoop version | Align the jar versions in the Spark images with the Spark/Hadoop version |
