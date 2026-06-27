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
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    from_json, col, window, avg, min as spark_min, max as spark_max,
    stddev, count, to_timestamp, lit
)
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType
import os
import uuid
import psycopg2
from psycopg2.extras import execute_values


# Columns written to each tenant's aggregation table, in order.
AGG_COLUMNS = [
    "time", "sensor_id", "equipment_id",
    "avg_value", "min_value", "max_value", "stddev_value",
    "count_values", "anomaly_count", "anomaly_percentage",
]
# Natural key used for ON CONFLICT (must match the table's UNIQUE constraint).
AGG_CONFLICT_KEY = ("time", "sensor_id", "equipment_id")


def company_id_to_schema(company_id):
    """
    Convert a company_id UUID to its tenant schema name.

    company_id arrives in untrusted Kafka payloads, so it is validated as a UUID before
    the schema/table name is built; a non-UUID raises ValueError instead of producing an
    arbitrary JDBC target (defends against injection — see ADR-0003).
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


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


def write_aggregations_to_db(batch_df, batch_id, aggregation_table):
    """
    Idempotently upsert an aggregation batch into each tenant's schema (ADR-0006).

    Each window is keyed by (time, sensor_id, equipment_id); on conflict the row is
    updated, so a retried micro-batch (expected under the at-least-once pipeline,
    ADR-0005) does not create duplicates. Any failure re-raises so Spark fails the batch
    and retries it, rather than silently dropping data (the upsert makes that retry safe).
    """
    rows = batch_df.collect()
    if not rows:
        return

    # Group rows by tenant so each is written to its own schema.
    by_company = {}
    for r in rows:
        company_id = r["company_id"]
        if not company_id:
            continue
        by_company.setdefault(company_id, []).append(r)

    if not by_company:
        return

    update_cols = [c for c in AGG_COLUMNS if c not in AGG_CONFLICT_KEY]
    cols_sql = ", ".join(AGG_COLUMNS)
    set_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    conflict_sql = ", ".join(AGG_CONFLICT_KEY)

    conn = _db_connection()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            for company_id, tenant_rows in by_company.items():
                schema_name = company_id_to_schema(company_id)  # validates the UUID
                target = f'"{schema_name}"."{aggregation_table}"'
                values = [
                    (
                        r["time"], str(r["sensor_id"]), str(r["equipment_id"]),
                        r["avg_value"], r["min_value"], r["max_value"], r["stddev_value"],
                        r["count_values"], r["anomaly_count"], r["anomaly_percentage"],
                    )
                    for r in tenant_rows
                ]
                sql = (
                    f"INSERT INTO {target} ({cols_sql}) VALUES %s "
                    f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {set_sql}"
                )
                execute_values(cur, sql, values)
                print(f"Batch {batch_id}: upserted {len(values)} rows into {target}")
        conn.commit()
    except Exception:
        conn.rollback()
        # Re-raise so Spark fails the batch and retries it (ADR-0006). The upsert above is
        # idempotent, so re-processing the same window is safe.
        raise
    finally:
        conn.close()


def create_aggregation_stream(spark, window_duration, watermark_delay, table_name):
    """Create a streaming windowed aggregation for one time window."""

    # Schema for Kafka messages
    schema = StructType([
        StructField("timestamp", StringType(), True),
        StructField("sensor_id", StringType(), True),
        StructField("equipment_id", StringType(), True),
        StructField("site_id", StringType(), True),
        StructField("company_id", StringType(), True),
        StructField("value", DoubleType(), True),
        StructField("unit", StringType(), True),
        StructField("quality_code", IntegerType(), True)
    ])

    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    df = spark \
        .readStream \
        .format("kafka") \
        .option("kafka.bootstrap.servers", kafka_bootstrap) \
        .option("subscribe", "sensor-data-raw") \
        .option("startingOffsets", "latest") \
        .option("maxOffsetsPerTrigger", "10000") \
        .load()

    parsed_df = df.select(
        from_json(col("value").cast("string"), schema).alias("data")
    ).select("data.*")

    parsed_df = parsed_df.withColumn("time", to_timestamp(col("timestamp")))

    # Watermark bounds the aggregation state and defines how late data may arrive before
    # its window is closed (ADR-0006). Append mode then emits each window once, when the
    # watermark passes its end.
    aggregated_df = parsed_df \
        .withWatermark("time", watermark_delay) \
        .groupBy(
            window(col("time"), window_duration),
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

    # Durable checkpoint location so a restart resumes from committed offsets/state
    # (ADR-0006). Defaults to the persistent path provisioned in the image, not /tmp.
    checkpoint_base = os.getenv("CHECKPOINT_LOCATION", "/opt/spark/checkpoints")

    query = aggregated_df \
        .writeStream \
        .outputMode("append") \
        .foreachBatch(lambda df, batch_id: write_aggregations_to_db(df, batch_id, table_name)) \
        .trigger(processingTime='5 seconds') \
        .option("checkpointLocation", f"{checkpoint_base}/{table_name}") \
        .start()

    return query


if __name__ == "__main__":
    builder = SparkSession.builder \
        .appName("IndustryFlow-Aggregations") \
        .master(os.getenv("SPARK_MASTER", "local[*]")) \
        .config("spark.jars.packages",
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
                "org.postgresql:postgresql:42.6.0") \
        .config("spark.sql.shuffle.partitions",
                os.getenv("SPARK_SQL_SHUFFLE_PARTITIONS", "200"))
    # Resource budget on a shared standalone cluster: cap this app's cores so streaming and
    # aggregations coexist on one worker (the deployment sets the split, so it scales). Unset
    # = Spark default (grab all cores). NOTE: spark.sql.shuffle.partitions is pinned into a
    # streaming query's checkpoint — changing it requires a fresh checkpoint.
    cores_max = os.getenv("SPARK_CORES_MAX")
    if cores_max:
        builder = builder.config("spark.cores.max", cores_max)
    spark = builder.getOrCreate()

    spark.sparkContext.setLogLevel("WARN")

    print("Starting aggregation streaming jobs with schema-per-tenant routing...")
    print("=" * 60)

    # One query per window. Watermark (allowed lateness) is configurable per window.
    queries = [
        create_aggregation_stream(
            spark, "1 minute", os.getenv("WATERMARK_1MIN", "2 minutes"),
            "sensor_aggregations_1min"),
        create_aggregation_stream(
            spark, "5 minutes", os.getenv("WATERMARK_5MIN", "10 minutes"),
            "sensor_aggregations_5min"),
        create_aggregation_stream(
            spark, "1 hour", os.getenv("WATERMARK_1HOUR", "15 minutes"),
            "sensor_aggregations_1hour"),
    ]
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
