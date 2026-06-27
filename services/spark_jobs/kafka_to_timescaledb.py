# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Spark Structured Streaming Job: Kafka to TimescaleDB
Reads sensor data from Kafka and writes to TimescaleDB in real-time
Schema-per-tenant architecture: Routes data to tenant_<uuid>.sensor_measurements
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import from_json, col, to_timestamp
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType
import logging
import os
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_spark_session(app_name="IndustryFlow-Streaming"):
    """Create and configure Spark session with Kafka support"""
    builder = SparkSession.builder \
        .appName(app_name) \
        .master(os.getenv("SPARK_MASTER", "local[*]")) \
        .config("spark.jars.packages",
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
                "org.postgresql:postgresql:42.6.0") \
        .config("spark.sql.streaming.checkpointLocation",
                os.getenv("CHECKPOINT_LOCATION", "/opt/spark/checkpoints")) \
        .config("spark.streaming.stopGracefullyOnShutdown", "true") \
        .config("spark.sql.shuffle.partitions",
                os.getenv("SPARK_SQL_SHUFFLE_PARTITIONS", "200"))
    # Resource budget on a shared standalone cluster: cap this app's cores so it does not
    # starve the aggregation app on the same worker. The deployment sets the split (compose),
    # so it scales — raise the caps or add workers. Unset = Spark default (grab all cores).
    cores_max = os.getenv("SPARK_CORES_MAX")
    if cores_max:
        builder = builder.config("spark.cores.max", cores_max)
    spark = builder.getOrCreate()

    logger.info(f"Spark session created: {spark.version}")
    return spark


def define_sensor_schema():
    """Define the schema for incoming sensor messages from Kafka"""
    return StructType([
        StructField("timestamp", StringType(), False),
        StructField("sensor_id", StringType(), False),
        StructField("equipment_id", StringType(), False),
        StructField("site_id", StringType(), False),
        StructField("company_id", StringType(), True),
        StructField("value", DoubleType(), False),
        StructField("unit", StringType(), True),
        StructField("quality_code", IntegerType(), True)
    ])


def company_id_to_schema(company_id):
    """
    Convert a company_id UUID to its tenant schema name.

    company_id arrives in untrusted Kafka payloads, so it is validated as a UUID before
    the schema/table name is built; a non-UUID raises ValueError instead of producing an
    arbitrary JDBC target (defends against injection — see ADR-0003).
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


MEASUREMENT_COLUMNS = ["time", "sensor_id", "equipment_id", "site_id", "value", "unit", "quality_code"]


def _make_measurement_upsert(db_cfg, schema_name):
    """
    Build a foreachPartition function that upserts measurement rows into a tenant schema
    with ON CONFLICT (time, sensor_id) DO NOTHING — so a retried or redelivered batch does
    not duplicate readings (ADR-0006). It runs on the executors, one connection per
    partition, which keeps this high-volume write distributed (not collected to the driver).
    """
    cols = ", ".join(MEASUREMENT_COLUMNS)
    sql = (
        f'INSERT INTO "{schema_name}".sensor_measurements ({cols}) VALUES %s '
        f'ON CONFLICT (time, sensor_id) DO NOTHING'
    )

    def _upsert(rows):
        import psycopg2
        from psycopg2.extras import execute_values
        from upsert_filter import split_known_sensors  # shipped via --py-files

        conn = psycopg2.connect(**db_cfg)
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                # Drop rows whose sensor_id is not provisioned, so one orphan FK row does not
                # abort the whole batch and wedge the stream (ADR-0006). The valid set is read
                # per partition so newly-created sensors are picked up on the next batch.
                cur.execute(f'SELECT sensor_id FROM "{schema_name}".sensors')
                valid = {str(r[0]) for r in cur.fetchall()}
                tuples = [
                    (r["time"], r["sensor_id"], r["equipment_id"],
                     r["site_id"], r["value"], r["unit"], r["quality_code"])
                    for r in rows
                ]
                kept, skipped = split_known_sensors(tuples, valid)
                for i in range(0, len(kept), 5000):
                    execute_values(cur, sql, kept[i:i + 5000])
            conn.commit()
            if skipped:
                logger.warning(
                    "%s: skipped %d measurement row(s) with unknown sensor_id "
                    "(not provisioned)", schema_name, skipped
                )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return _upsert


def write_to_timescaledb(batch_df, batch_id):
    """
    Write each micro-batch to TimescaleDB with schema-per-tenant routing, idempotently.

    Rows are routed by company_id to the tenant schema and upserted with ON CONFLICT
    (time, sensor_id) DO NOTHING via a distributed foreachPartition, so a Spark batch retry
    or an at-least-once redelivery (ADR-0005) does not create duplicate readings (ADR-0006).
    A write failure re-raises so Spark fails and retries the batch.
    """
    db_cfg = {
        "host": os.getenv("TIMESCALEDB_HOST", "localhost"),
        "port": os.getenv("TIMESCALEDB_PORT", "5432"),
        "dbname": os.getenv("TIMESCALEDB_DB", "industryflow"),
        "user": os.getenv("SPARK_STREAMING_DB_USER", "spark_streaming_user"),
        "password": os.getenv("SPARK_STREAMING_DB_PASSWORD"),
    }

    company_ids = [
        r["company_id"]
        for r in batch_df.select("company_id").distinct().collect()
        if r["company_id"]
    ]
    if not company_ids:
        logger.info(f"Batch {batch_id} is empty, skipping write")
        return

    for company_id in company_ids:
        schema_name = company_id_to_schema(company_id)  # validates the UUID
        tenant_data = batch_df.filter(col("company_id") == company_id).select(*MEASUREMENT_COLUMNS)
        try:
            tenant_data.foreachPartition(_make_measurement_upsert(db_cfg, schema_name))
            logger.info(f"Batch {batch_id}: upserted measurements into {schema_name}.sensor_measurements")
        except Exception as e:
            logger.error(f"Batch {batch_id}: failed writing to {schema_name}.sensor_measurements: {e}")
            raise


def main():
    """Main streaming application"""
    logger.info("Starting Kafka to TimescaleDB streaming job...")
    logger.info("Architecture: Schema-per-tenant with dynamic routing")

    # Create Spark session
    spark = create_spark_session()

    # Define schema
    sensor_schema = define_sensor_schema()

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

    # Parse JSON data and transform
    # Note: to_timestamp() automatically parses ISO8601 format with timezone
    parsed_stream = raw_stream \
        .selectExpr("CAST(value AS STRING) as json_string") \
        .select(from_json(col("json_string"), sensor_schema).alias("data")) \
        .select("data.*") \
        .withColumn("time", to_timestamp(col("timestamp"))) \
        .select(
            col("time"),
            col("sensor_id"),
            col("equipment_id"),
            col("site_id"),
            col("company_id"),
            col("value"),
            col("unit"),
            col("quality_code")
        )

    # Write to TimescaleDB using foreachBatch with tenant routing. Durable checkpoint
    # location so a restart resumes from committed offsets, not /tmp (ADR-0006).
    checkpoint_base = os.getenv("CHECKPOINT_LOCATION", "/opt/spark/checkpoints")

    query = parsed_stream \
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
