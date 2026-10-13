# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Spark Structured Streaming Job: Kafka to TimescaleDB
Reads sensor data from Kafka and writes to TimescaleDB in real-time
Schema-per-tenant architecture: Routes data to tenant_<uuid>.sensor_measurements

Readings that can never be written are dead-lettered, not retried forever and not dropped silently
(ADR-0005 dec 3; see reading_guard.py). This job is the dead-letter owner for invalid readings on
``sensor-data-raw``: it consumes every message from the earliest offset, and the aggregation job
excludes the same readings by the same definition without publishing them a second time.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import broadcast, col
import logging
import os

from reading_guard import (  # shipped via --py-files
    DEFAULT_DLQ_TOPIC, SOURCE_COLUMNS, UNKNOWN_TENANT, canonical_uuid, classify_references,
    company_id_to_schema,
    parse_readings, publish_dead_letters, rejection_reason,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DLQ_CONSUMER = "spark-streaming"


def configure_s3a(builder, checkpoint_location):
    """
    Configure the S3A filesystem when the checkpoint lives on object storage (s3a://…).

    A multi-worker standalone cluster (or spark-on-k8s) can't share a local checkpoint volume:
    the executor writes Structured-Streaming state-store deltas and the driver reads them, so the
    checkpoint must sit on ONE store visible to every node. S3A/MinIO is that store — see
    docs/architecture/stream-processing.md. For a local path this is a no-op, so local/dev runs
    are unaffected. Credentials/endpoint default to the in-cluster MinIO.

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


def create_spark_session(app_name="IndustryFlow-Streaming"):
    """Create and configure Spark session with Kafka support"""
    # SPARK_MASTER is the single source of truth for the master (spark-submit no longer passes
    # --master); defaults to local[*] when unset. The Kafka connector is NOT named here: the image's
    # spark-submit `--packages` provides it, pinned to the image's Spark version (a coordinate in
    # code would be one more copy of that version to drift — scripts/check_spark_version_parity.py).
    checkpoint_location = os.getenv("CHECKPOINT_LOCATION", "/opt/spark/checkpoints")
    builder = SparkSession.builder \
        .appName(app_name) \
        .master(os.getenv("SPARK_MASTER", "local[*]")) \
        .config("spark.sql.streaming.checkpointLocation", checkpoint_location) \
        .config("spark.streaming.stopGracefullyOnShutdown", "true") \
        .config("spark.sql.shuffle.partitions",
                os.getenv("SPARK_SQL_SHUFFLE_PARTITIONS", "200")) \
        .config("spark.ui.prometheus.enabled", "true")
    # S3A checkpoint store (multi-worker / spark-on-k8s); no-op for a local checkpoint path.
    builder = configure_s3a(builder, checkpoint_location)
    # Resource budget on a shared standalone cluster: cap this app's cores so it does not
    # starve the aggregation app on the same worker. The deployment sets the split (compose),
    # so it scales — raise the caps or add workers. Unset = Spark default (grab all cores).
    cores_max = os.getenv("SPARK_CORES_MAX")
    if cores_max:
        builder = builder.config("spark.cores.max", cores_max)
    spark = builder.getOrCreate()

    logger.info(f"Spark session created: {spark.version}")
    return spark


MEASUREMENT_COLUMNS = ["time", "sensor_id", "equipment_id", "site_id", "value", "unit", "quality_code"]


def _db_config():
    return {
        "host": os.getenv("TIMESCALEDB_HOST", "localhost"),
        "port": os.getenv("TIMESCALEDB_PORT", "5432"),
        "dbname": os.getenv("TIMESCALEDB_DB", "industryflow"),
        "user": os.getenv("SPARK_STREAMING_DB_USER", "spark_streaming_user"),
        "password": os.getenv("SPARK_STREAMING_DB_PASSWORD"),
        # TLS to the DB (ADR-0017). libpq reads sslmode/sslrootcert directly.
        "sslmode": os.getenv("DB_SSLMODE", "verify-full"),
        "sslrootcert": os.getenv("DB_SSLROOTCERT", "/etc/ssl/industryflow/ca.crt"),
    }


def _make_measurement_upsert(db_cfg, schema_name):
    """
    Build a foreachPartition function that writes measurement rows into a tenant schema with
    ON CONFLICT (time, sensor_id) DO NOTHING — the table's natural key — so a retried or
    redelivered batch does not duplicate readings (ADR-0006 dec 3). A raw reading is written once
    and never rewritten: a redelivery under ADR-0005 carries the same reading. It runs on the
    executors, one connection per partition, which keeps this high-volume write distributed (not
    collected to the driver). Every row reaching it has passed reading_guard, so a failure here is
    an infrastructure failure and re-raises (ADR-0006 dec 4).
    """
    cols = ", ".join(MEASUREMENT_COLUMNS)
    sql = (
        f'INSERT INTO "{schema_name}".sensor_measurements ({cols}) VALUES %s '
        f'ON CONFLICT (time, sensor_id) DO NOTHING'
    )

    def _upsert(rows):
        import psycopg2
        from psycopg2.extras import execute_values

        tuples = [
            (r["time"], r["sensor_id"], r["equipment_id"],
             r["site_id"], r["value"], r["unit"], r["quality_code"])
            for r in rows
        ]
        if not tuples:
            return
        conn = psycopg2.connect(**db_cfg)
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                for i in range(0, len(tuples), 5000):
                    execute_values(cur, sql, tuples[i:i + 5000])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return _upsert


def lookup_references(db_cfg, keys):
    """
    Read, once per tenant per batch, what `classify_references` needs: whether the tenant's schema
    exists and which of the batch's sensor/equipment ids exist in it. Catalog reads, so a tenant
    with no schema yields False rather than an error.
    """
    import psycopg2

    by_company = {}
    for company_id, sensor_id, equipment_id in keys:
        sensors, equipment = by_company.setdefault(company_id, (set(), set()))
        sensors.add(canonical_uuid(sensor_id))
        equipment.add(canonical_uuid(equipment_id))

    tenant_exists, known_sensors, known_equipment = {}, {}, {}
    conn = psycopg2.connect(**db_cfg)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for company_id, (sensors, equipment) in by_company.items():
                schema = company_id_to_schema(company_id)
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname = 'sensor_measurements')",
                    (schema,),
                )
                tenant_exists[company_id] = bool(cur.fetchone()[0])
                if not tenant_exists[company_id]:
                    continue
                cur.execute(f'SELECT sensor_id::text FROM "{schema}".sensors '
                            f'WHERE sensor_id = ANY(%s::uuid[])', (sorted(sensors),))
                known_sensors[company_id] = {r[0] for r in cur.fetchall()}
                cur.execute(f'SELECT equipment_id::text FROM "{schema}".equipment '
                            f'WHERE equipment_id = ANY(%s::uuid[])', (sorted(equipment),))
                known_equipment[company_id] = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    return tenant_exists, known_sensors, known_equipment


def process_batch(batch_df, batch_id, *, lookup, dead_letter, upsert):
    """
    Route one micro-batch: dead-letter what cannot be written, then write the rest per tenant.

    ``batch_df`` carries the parsed readings and their stateless ``reject_reason``. ``lookup(keys)``
    returns the referential facts for the batch's keys, ``dead_letter(df)`` publishes rejected rows,
    and ``upsert(tenant_df, schema)`` writes one tenant's rows; they are injected so the routing is
    testable without a database or a broker. Dead letters are published first: if that fails the
    batch fails and nothing advances. Any failure re-raises so Spark retries the batch.
    """
    batch_df = batch_df.persist()
    try:
        rejected = batch_df.filter(col("reject_reason").isNotNull())
        valid = batch_df.filter(col("reject_reason").isNull()).drop("reject_reason")

        keys = [tuple(r) for r in valid.select("company_id", "sensor_id", "equipment_id")
                .distinct().collect()]
        refused = classify_references(keys, *lookup(keys)) if keys else []
        if refused:
            spark = batch_df.sparkSession
            refused_df = spark.createDataFrame(
                refused, "company_id string, sensor_id string, equipment_id string, reject_reason string")
            tagged = valid.join(broadcast(refused_df), ["company_id", "sensor_id", "equipment_id"], "left")
            rejected = rejected.unionByName(tagged.filter(col("reject_reason").isNotNull()))
            valid = tagged.filter(col("reject_reason").isNull()).drop("reject_reason")

        if not rejected.isEmpty():
            counts = {r["reject_reason"]: r["count"]
                      for r in rejected.groupBy("reject_reason").count().collect()}
            dead_letter(rejected)
            logger.warning(f"Batch {batch_id}: dead-lettered readings by reason: {counts}")

        no_schema = {r[0] for r in refused if r[3] == UNKNOWN_TENANT}
        for company_id in sorted({k[0] for k in keys} - no_schema):
            schema_name = company_id_to_schema(company_id)
            tenant_data = valid.filter(col("company_id") == company_id).select(*MEASUREMENT_COLUMNS)
            try:
                upsert(tenant_data, schema_name)
                logger.info(f"Batch {batch_id}: upserted measurements into {schema_name}.sensor_measurements")
            except Exception as e:
                logger.error(f"Batch {batch_id}: failed writing to {schema_name}.sensor_measurements: {e}")
                raise
    finally:
        batch_df.unpersist()


def write_to_timescaledb(batch_df, batch_id):
    """foreachBatch entry point: `process_batch` wired to TimescaleDB and the dead-letter topic."""
    db_cfg = _db_config()
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    dlq_topic = os.getenv("KAFKA_TOPIC_MEASUREMENTS_DLQ", DEFAULT_DLQ_TOPIC)
    process_batch(
        batch_df, batch_id,
        lookup=lambda keys: lookup_references(db_cfg, keys),
        dead_letter=lambda df: publish_dead_letters(df, DLQ_CONSUMER, bootstrap, dlq_topic),
        upsert=lambda df, schema: df.foreachPartition(_make_measurement_upsert(db_cfg, schema)),
    )


def build_readings(kafka_df):
    """Parsed readings, each with its stateless ``reject_reason`` (null when writable)."""
    return parse_readings(kafka_df).withColumn("reject_reason", rejection_reason()).select(
        *SOURCE_COLUMNS, "time", "sensor_id", "equipment_id", "site_id", "company_id",
        "value", "unit", "quality_code", "reject_reason",
    )


def main():
    """Main streaming application"""
    logger.info("Starting Kafka to TimescaleDB streaming job...")
    logger.info("Architecture: Schema-per-tenant with dynamic routing")

    # Create Spark session
    spark = create_spark_session()

    # Get Kafka config from environment
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    logger.info(f"Connecting to Kafka at: {kafka_bootstrap}")

    # Read from Kafka
    raw_stream = spark \
        .readStream \
        .format("kafka") \
        .option("kafka.bootstrap.servers", kafka_bootstrap) \
        .option("subscribe", "sensor-data-raw") \
        .option("startingOffsets", "earliest") \
        .option("maxOffsetsPerTrigger", "10000") \
        .option("kafka.max.partition.fetch.bytes", "1048576") \
        .load()

    logger.info("Successfully connected to Kafka")

    # Write to TimescaleDB using foreachBatch with tenant routing. Durable checkpoint
    # location so a restart resumes from committed offsets, not /tmp (ADR-0006).
    checkpoint_base = os.getenv("CHECKPOINT_LOCATION", "/opt/spark/checkpoints")

    query = build_readings(raw_stream) \
        .writeStream \
        .outputMode("append") \
        .foreachBatch(write_to_timescaledb) \
        .trigger(processingTime="2 seconds") \
        .option("checkpointLocation", f"{checkpoint_base}/sensor_measurements") \
        .start()

    logger.info("Streaming query started with tenant routing. Waiting for data...")

    # Wait for termination
    query.awaitTermination()


if __name__ == "__main__":
    main()
