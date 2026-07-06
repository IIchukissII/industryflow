<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Stream processing

The path from a sensor reading to stored, queryable time-series: ingestion → Kafka → Spark
Structured Streaming → TimescaleDB. The Spark job code under `services/spark_jobs/` is
authoritative; this is the shape and its delivery guarantees.

## Pipeline

```
device ──mTLS──▶ ingestion ──▶ Kafka (sensor-data-raw) ──▶ Spark streaming ──▶ TimescaleDB
                                                              │
                                                              └─▶ Spark aggregations ──▶ TimescaleDB
```

1. **Ingestion** validates the reading, stamps it with the verified `company_id`, and produces
   to the raw-sensor Kafka topic. It never writes to the database directly.
2. **Spark streaming** consumes the topic and upserts raw readings into each tenant's
   `sensor_measurements`.
3. **Spark aggregations** windows the stream into 1-minute / 5-minute / 1-hour rollups and
   upserts them into the aggregation hypertables.

## Read side (live values)

The write path above ends at TimescaleDB; the live sensor view reads back out of it:

```
sensor_measurements ─▶ api-gateway cache_updater (2s poll) ─▶ Redis (sensor:latest:*, TTL 60s) ─▶ WS /ws/sensors ─▶ UI
```

The `cache_updater` polls each tenant's `sensor_measurements` for the latest value per sensor in
the last hour and mirrors it into Redis; the frontend streams those over the sensor WebSocket. So
the **live UI view depends on the Spark streaming job** (pipeline step 2) writing fresh
measurements — if that job is stopped, `sensor_measurements` goes stale, the cache empties, and
the UI shows no data even though ingestion and Kafka are fine. The gateway logs a throttled
warning in that state (`0 fresh sensor_measurements … is spark-streaming running?`). Note this is
a distinct path from the aggregation rollups, which feed model **feature baselines**, not the live
view (**[ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md)**).

## Delivery semantics (at-least-once, idempotent)

The system is **at-least-once with idempotent writes**, so a retry or replay cannot create
duplicates or corrupt state:

- **Kafka consumption** is at-least-once: manual offset commits *after* a batch is fully
  handled, a bounded per-message retry, and a **dead-letter topic** for messages that cannot be
  processed — offsets only advance once the batch is durably handled
  (**[ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md)**).
- **Spark writes** are idempotent upserts: each window is emitted once via a **watermark +
  append** output mode (no unbounded state, no re-emitting old windows), and rows are written
  with `ON CONFLICT … DO UPDATE/NOTHING` against the tables' `UNIQUE` constraints (which include
  the hypertable partition column). Writes go through `foreachPartition` with a per-executor
  connection, and an error re-raises so Spark retries safely
  (**[ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md)**).
- **Checkpoints** are durable, so a restart resumes the stream rather than reprocessing from the
  beginning. The **stateful** aggregation queries (windowed) keep a *state store* in the
  checkpoint that the executor writes and the driver reads, so the checkpoint must live on **one
  store visible to every node**.

  In compose the checkpoint is on **MinIO via S3A** (`CHECKPOINT_LOCATION=s3a://spark-checkpoints`),
  shared over the network by the driver (the `spark-*` job containers) and every executor (the
  `spark-worker` pool) — so the standalone cluster **scales to N workers** (`SPARK_WORKER_REPLICAS`
  / `docker compose up -d --scale spark-worker=N`) with no shared local volume. The job's
  SparkSession turns S3A on whenever `CHECKPOINT_LOCATION` is an `s3a://` URI (a local path opts
  out for single-node dev); the S3A jars (`hadoop-aws` + `aws-java-sdk-bundle`, pinned to the
  image's Hadoop 3.3.4) are baked into all three Spark images, and a `minio-init` one-shot creates
  the bucket. (Raw streaming is stateless, so it is unaffected either way.)

  Under **Helm** Spark runs in local mode (driver = executor, one pod), so its RWO PVC at
  `/opt/spark/checkpoints` suffices by default. To scale Spark out on k8s, point
  `config.CHECKPOINT_LOCATION` at `s3a://…`, give the spark workloads the MinIO credentials, and
  disable their `persistence` — see the chart README.

## Tenant routing

Each Kafka message carries the verified `company_id`. The Spark sink resolves it to the tenant
schema (UUID-validated, `tenant_<uuid>`) before writing — the same tenant-resolution discipline
as the rest of the platform (**[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**).
