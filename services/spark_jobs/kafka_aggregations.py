# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Spark Structured Streaming: Sensor Aggregations
Creates 1-minute, 5-minute, and 1-hour aggregations from the Kafka stream.
Schema-per-tenant architecture: routes to tenant_<uuid>.sensor_aggregations_*.

Windowing and writes follow ADR-0006: a watermark bounds the streaming state, each
window is emitted once (append mode) when it closes, and writes are idempotent upserts on
(time, sensor_id, equipment_id) so retries and redeliveries do not duplicate rows.

Readings that cannot be written are excluded *before* the watermark by the same definition the
streaming job applies (reading_guard.py) — a far-future timestamp would otherwise drag the watermark
forward and close every live window early. The streaming job, which consumes every message, is the
one that dead-letters them; this job does not publish them a second time. A reading naming a
tenant, sensor or equipment the platform does not know is excluded the same way, at write time: its
key is the aggregate's key, so dropping the aggregate excludes exactly those readings. The window widths and
allowed lateness are the train/serve binding (ADR-0023 rev 2 dec 9), defined once in the shared
window contract (industryflow_contracts.windows, ADR-0006 dec 7); aggregation_windows.py only adapts
it to Spark's duration strings.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col, window, avg, min as spark_min, max as spark_max,
    stddev, count, lit
)
import os
import psycopg2
from psycopg2.extras import execute_values

from aggregation_windows import AGGREGATION_WINDOWS, table_name, watermark_delay, window_duration
from reading_guard import (
    DEFAULT_SOURCE_TOPIC, canonical_uuid, classify_references, company_id_to_schema, parse_readings,
    read_tenant_references, rejection_reason,
)


# Columns written to each tenant's aggregation table, in order.
AGG_COLUMNS = [
    "time", "sensor_id", "equipment_id",
    "avg_value", "min_value", "max_value", "stddev_value",
    "count_values", "anomaly_count", "anomaly_percentage",
]
# Natural key used for ON CONFLICT (must match the table's UNIQUE constraint).
AGG_CONFLICT_KEY = ("time", "sensor_id", "equipment_id")


def configure_s3a(builder, checkpoint_location):
    """
    Configure the S3A filesystem when the checkpoint lives on object storage (s3a://…).

    The stateful (windowed) aggregations keep a state store in the checkpoint that the executor
    writes and the driver reads, so on a multi-worker cluster the checkpoint must sit on ONE
    store visible to every node — S3A/MinIO (docs/architecture/stream-processing.md). For a
    local path this is a no-op. Credentials/endpoint default to the in-cluster MinIO.

    Spark 4's Hadoop 3.4 S3A runs on the AWS SDK v2, which requires a region even against a custom
    endpoint like MinIO (it fails to start otherwise); AWS_REGION supplies one, defaulting to a
    placeholder MinIO ignores.
    """
    if not str(checkpoint_location).startswith("s3a://"):
        return builder
    endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
    access = os.getenv("MINIO_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret = os.getenv("MINIO_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    builder = builder \
        .config("spark.hadoop.fs.s3a.endpoint", endpoint.split("://", 1)[-1]) \
        .config("spark.hadoop.fs.s3a.connection.ssl.enabled",
                "true" if endpoint.startswith("https://") else "false") \
        .config("spark.hadoop.fs.s3a.path.style.access", "true") \
        .config("spark.hadoop.fs.s3a.endpoint.region",
                os.getenv("AWS_REGION", "us-east-1")) \
        .config("spark.hadoop.fs.s3a.aws.credentials.provider",
                "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider")
    if access and secret:
        builder = builder \
            .config("spark.hadoop.fs.s3a.access.key", access) \
            .config("spark.hadoop.fs.s3a.secret.key", secret)
    return builder


def _db_connection():
    """Open a psycopg2 connection to TimescaleDB using the streaming role."""
    return psycopg2.connect(
        host=os.getenv("TIMESCALEDB_HOST", "localhost"),
        port=os.getenv("TIMESCALEDB_PORT", "5432"),
        dbname=os.getenv("TIMESCALEDB_DB", "industryflow"),
        user=os.getenv("SPARK_STREAMING_DB_USER", "spark_streaming_user"),
        password=os.getenv("SPARK_STREAMING_DB_PASSWORD"),
        # TLS to the DB (ADR-0017); libpq reads these directly.
        sslmode=os.getenv("DB_SSLMODE", "verify-full"),
        sslrootcert=os.getenv("DB_SSLROOTCERT", "/etc/ssl/industryflow/ca.crt"),
    )


def _upsert_values(cur, target, values):
    update_cols = [c for c in AGG_COLUMNS if c not in AGG_CONFLICT_KEY]
    sql = (
        f"INSERT INTO {target} ({', '.join(AGG_COLUMNS)}) VALUES %s "
        f"ON CONFLICT ({', '.join(AGG_CONFLICT_KEY)}) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    )
    execute_values(cur, sql, values)


def write_aggregations_to_db(batch_df, batch_id, aggregation_table, *,
                             connect=None, references=read_tenant_references, upsert=_upsert_values):
    """
    Idempotently upsert an aggregation batch into each tenant's schema (ADR-0006).

    Each window is keyed by (time, sensor_id, equipment_id); on conflict the row is
    updated, so a retried micro-batch (expected under the at-least-once pipeline,
    ADR-0005) does not create duplicates. Each tenant is written in its own transaction. A tenant
    whose schema does not exist, and an aggregate whose sensor or equipment the tenant does not
    know, are skipped and logged — they are the readings the streaming job dead-letters
    (reading_guard.classify_references, one definition for both jobs; ADR-0006 rev 2 dec 4) — so
    one unknown key cannot fail every tenant's batch. Any other failure re-raises so Spark fails the batch and retries it, rather than
    silently dropping data (the upsert makes that retry safe; ADR-0006 dec 4).
    """
    rows = batch_df.collect()
    if not rows:
        return

    # Group rows by tenant so each is written to its own schema. Every row passed reading_guard
    # before aggregation, so company_id is present and a well-formed UUID.
    by_company = {}
    for r in rows:
        by_company.setdefault(r["company_id"], []).append(r)

    conn = (connect or _db_connection)()
    try:
        conn.autocommit = False
        for company_id, tenant_rows in sorted(by_company.items()):
            schema_name = company_id_to_schema(company_id)  # validates the UUID
            with conn.cursor() as cur:
                keys = sorted({(r["company_id"], r["sensor_id"], r["equipment_id"]) for r in tenant_rows})
                exists, known_sensors, known_equipment = references(
                    cur, schema_name,
                    {canonical_uuid(k[1]) for k in keys}, {canonical_uuid(k[2]) for k in keys})
                refused = {k[:3]: k[3] for k in classify_references(
                    keys, {company_id: exists}, {company_id: known_sensors},
                    {company_id: known_equipment})}
                if refused:
                    tenant_rows = [r for r in tenant_rows
                                   if (r["company_id"], r["sensor_id"], r["equipment_id"]) not in refused]
                    print(f"Batch {batch_id}: excluded {len(refused)} {aggregation_table} key(s) in "
                          f"{schema_name} ({', '.join(sorted(set(refused.values())))}; "
                          f"their raw readings are dead-lettered)")
                if not tenant_rows:
                    conn.rollback()
                    continue
                target = f'"{schema_name}"."{aggregation_table}"'
                values = [
                    (
                        r["time"], str(r["sensor_id"]), str(r["equipment_id"]),
                        r["avg_value"], r["min_value"], r["max_value"], r["stddev_value"],
                        r["count_values"], r["anomaly_count"], r["anomaly_percentage"],
                    )
                    for r in tenant_rows
                ]
                upsert(cur, target, values)
            conn.commit()
            print(f"Batch {batch_id}: upserted {len(values)} rows into {target}")
    except Exception:
        conn.rollback()
        # Re-raise so Spark fails the batch and retries it (ADR-0006). The upsert above is
        # idempotent, so re-processing the same window is safe.
        raise
    finally:
        conn.close()


def aggregate_readings(readings_df, granularity):
    """
    The windowed aggregation of one granularity over parsed readings (reading_guard.parse_readings).

    Rejected readings are removed BEFORE the watermark. The watermark is the maximum event time seen
    minus the allowed lateness, so one far-future timestamp would otherwise move it ahead and close —
    then drop as late — every live window. The watermark bounds the aggregation state and defines
    how late data may arrive before its window is closed (ADR-0006); append mode then emits each
    window once, when the watermark passes its end.
    """
    return readings_df \
        .filter(rejection_reason().isNull()) \
        .withWatermark("time", watermark_delay(granularity)) \
        .groupBy(
            window(col("time"), window_duration(granularity)),
            col("sensor_id"),
            col("equipment_id"),
            col("company_id")
        ) \
        .agg(
            avg("value").alias("avg_value"),
            spark_min("value").alias("min_value"),
            spark_max("value").alias("max_value"),
            stddev("value").alias("stddev_value"),
            count("value").alias("count_values")
        ) \
        .select(
            col("window.end").alias("time"),
            col("sensor_id"),
            col("equipment_id"),
            col("company_id"),
            col("avg_value"),
            col("min_value"),
            col("max_value"),
            col("stddev_value"),
            col("count_values").cast("int").alias("count_values"),
            lit(0).alias("anomaly_count"),
            lit(0.0).alias("anomaly_percentage")
        )


def create_aggregation_stream(spark, granularity):
    """Create a streaming windowed aggregation for one granularity (the shared window contract)."""
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    table = table_name(granularity)

    df = spark \
        .readStream \
        .format("kafka") \
        .option("kafka.bootstrap.servers", kafka_bootstrap) \
        .option("subscribe", os.getenv("KAFKA_TOPIC_SENSOR_DATA", DEFAULT_SOURCE_TOPIC)) \
        .option("startingOffsets", "latest") \
        .option("maxOffsetsPerTrigger", "10000") \
        .load()

    aggregated_df = aggregate_readings(parse_readings(df), granularity)

    # Durable checkpoint location so a restart resumes from committed offsets/state
    # (ADR-0006). Defaults to the persistent path provisioned in the image, not /tmp.
    checkpoint_base = os.getenv("CHECKPOINT_LOCATION", "/opt/spark/checkpoints")

    query = aggregated_df \
        .writeStream \
        .outputMode("append") \
        .foreachBatch(lambda df, batch_id: write_aggregations_to_db(df, batch_id, table)) \
        .trigger(processingTime='5 seconds') \
        .option("checkpointLocation", f"{checkpoint_base}/{table}") \
        .start()

    return query


if __name__ == "__main__":
    # The Kafka connector comes from the image's spark-submit `--packages`, pinned to the image's
    # Spark version — not from a coordinate here (scripts/check_spark_version_parity.py).
    builder = SparkSession.builder \
        .appName("IndustryFlow-Aggregations") \
        .master(os.getenv("SPARK_MASTER", "local[*]")) \
        .config("spark.sql.shuffle.partitions",
                os.getenv("SPARK_SQL_SHUFFLE_PARTITIONS", "200")) \
        .config("spark.ui.prometheus.enabled", "true")
    # Resource budget on a shared standalone cluster: cap this app's cores so streaming and
    # aggregations coexist on one worker (the deployment sets the split, so it scales). Unset
    # = Spark default (grab all cores). NOTE: spark.sql.shuffle.partitions is pinned into a
    # streaming query's checkpoint — changing it requires a fresh checkpoint.
    cores_max = os.getenv("SPARK_CORES_MAX")
    if cores_max:
        builder = builder.config("spark.cores.max", cores_max)
    # S3A checkpoint store (multi-worker / spark-on-k8s); no-op for a local checkpoint path.
    builder = configure_s3a(builder, os.getenv("CHECKPOINT_LOCATION", "/opt/spark/checkpoints"))
    spark = builder.getOrCreate()

    spark.sparkContext.setLogLevel("WARN")

    print("Starting aggregation streaming jobs with schema-per-tenant routing...")
    print("=" * 60)

    # One query per granularity. Width and allowed lateness are the train/serve binding, read from
    # the shared window contract (industryflow_contracts.windows), not per-deployment knobs.
    queries = [create_aggregation_stream(spark, g) for g in AGGREGATION_WINDOWS]
    print("✓ 1-minute, 5-minute and 1-hour aggregation streams started")
    print("=" * 60)

    # Await ALL queries: if any one dies, the process exits so the container's restart
    # policy and healthcheck observe it (ADR-0006).
    try:
        spark.streams.awaitAnyTermination()
    except KeyboardInterrupt:
        print("\nStopping all streams...")
        for q in queries:
            q.stop()
        print("All streams stopped.")
