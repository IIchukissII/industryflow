# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Spark Structured Streaming: Sensor Aggregations
Creates 1-minute, 5-minute, and 1-hour aggregations from Kafka stream
Schema-per-tenant architecture: Routes to tenant_<uuid>.sensor_aggregations_*
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    from_json, col, window, avg, min as spark_min, max as spark_max, 
    stddev, count, to_timestamp, lit, expr
)
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType
import os


def company_id_to_schema(company_id):
    """Convert company_id UUID to schema name"""
    schema_uuid = company_id.replace("-", "_")
    return f"tenant_{schema_uuid}"


def write_aggregations_to_db(batch_df, batch_id, aggregation_table):
    """
    Write aggregated batch to TimescaleDB with schema-per-tenant routing
    Groups by company_id and writes to respective tenant schemas
    """
    try:
        print(f"\n{'=' * 60}")
        print(f"Batch {batch_id} for {aggregation_table}")

        row_count = batch_df.count()
        print(f"Row count: {row_count}")

        if row_count == 0:
            print(f"Empty batch, skipping {aggregation_table}")
            print(f"{'=' * 60}\n")
            return

        print(f"Schema:")
        batch_df.printSchema()
        print("Sample data:")
        batch_df.show(3, truncate=False)

        # Get database config from environment
        db_host = os.getenv("TIMESCALEDB_HOST", "localhost")
        db_port = os.getenv("TIMESCALEDB_PORT", "5432")
        db_name = os.getenv("TIMESCALEDB_DB", "industryflow")
        db_user = os.getenv("SPARK_STREAMING_DB_USER", "spark_streaming_user")
        db_password = os.getenv("SPARK_STREAMING_DB_PASSWORD")

        jdbc_url = f"jdbc:postgresql://{db_host}:{db_port}/{db_name}"

        db_properties = {
            "user": db_user,
            "password": db_password,
            "driver": "org.postgresql.Driver",
            "batchsize": "5000",
            "reWriteBatchedInserts": "true",
            "stringtype": "unspecified"
        }

        # Group by company_id and write to respective tenant schemas
        company_ids = batch_df.select("company_id").distinct().collect()
        
        for row in company_ids:
            company_id = row["company_id"]
            if not company_id:
                print(f"Batch {batch_id}: Skipping rows with null company_id")
                continue
            
            # Filter data for this company
            tenant_data = batch_df.filter(col("company_id") == company_id)
            tenant_row_count = tenant_data.count()
            
            # Determine target schema and table
            schema_name = company_id_to_schema(company_id)
            full_table_name = f"{schema_name}.{aggregation_table}"
            
            print(f"Batch {batch_id}: Writing {tenant_row_count} rows to {full_table_name}")
            
            try:
                # Drop company_id column and cast UUIDs for PostgreSQL
                tenant_data_clean = tenant_data \
                    .withColumn("sensor_id", expr("CAST(sensor_id AS STRING)")) \
                    .withColumn("equipment_id", expr("CAST(equipment_id AS STRING)")) \
                    .drop("company_id")
                
                # Write to tenant-specific schema
                tenant_data_clean.write \
                    .format("jdbc") \
                    .option("url", jdbc_url) \
                    .option("dbtable", full_table_name) \
                    .option("user", db_user) \
                    .option("password", db_password) \
                    .option("driver", "org.postgresql.Driver") \
                    .option("batchsize", "5000") \
                    .option("reWriteBatchedInserts", "true") \
                    .option("stringtype", "unspecified") \
                    .mode("append") \
                    .save()

                print(f"✅ SUCCESS: Wrote {tenant_row_count} rows to {full_table_name}")
                
            except Exception as e:
                print(f"❌ ERROR writing to {full_table_name}")
                print(f"Error type: {type(e).__name__}")
                print(f"Error message: {str(e)}")
                import traceback
                traceback.print_exc()

        print(f"{'=' * 60}\n")

    except Exception as e:
        print(f"❌ ERROR processing batch {batch_id} for {aggregation_table}")
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"{'=' * 60}\n")


def create_aggregation_stream(spark, window_duration, table_name):
    """Create a streaming aggregation for a specific time window"""

    # Define schema for Kafka messages
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

    # Get Kafka config from environment
    kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    # Read from Kafka
    df = spark \
        .readStream \
        .format("kafka") \
        .option("kafka.bootstrap.servers", kafka_bootstrap) \
        .option("subscribe", "sensor-data-raw") \
        .option("startingOffsets", "latest") \
        .load()

    # Parse JSON and extract fields
    parsed_df = df.select(
        from_json(col("value").cast("string"), schema).alias("data")
    ).select("data.*")

    # Convert timestamp string to TimestampType (to_timestamp() handles ISO8601 with timezone)
    parsed_df = parsed_df.withColumn("time", to_timestamp(col("timestamp")))

    # Perform windowed aggregation
    aggregated_df = parsed_df \
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

    # Get checkpoint location from environment
    checkpoint_base = os.getenv("CHECKPOINT_LOCATION", "/tmp/spark-checkpoint")

    # Write to TimescaleDB with tenant routing
    query = aggregated_df \
        .writeStream \
        .outputMode("update") \
        .foreachBatch(lambda df, id: write_aggregations_to_db(df, id, table_name)) \
        .trigger(processingTime='5 seconds') \
        .option("spark.sql.streaming.minBatchesToRetain", "2") \
        .option("checkpointLocation", f"{checkpoint_base}-{table_name}") \
        .start()

    return query


if __name__ == "__main__":
    # Create Spark session
    spark = SparkSession.builder \
        .appName("IndustryFlow-Aggregations") \
        .master(os.getenv("SPARK_MASTER", "local[*]")) \
        .config("spark.jars.packages",
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
                "org.postgresql:postgresql:42.6.0") \
        .config("spark.sql.streaming.checkpointLocation",
                os.getenv("CHECKPOINT_LOCATION", "/tmp/spark-checkpoint-agg")) \
        .getOrCreate()

    spark.sparkContext.setLogLevel("WARN")

    print("Starting aggregation streaming jobs with schema-per-tenant routing...")
    print("=" * 60)

    # Create three parallel streaming queries for different time windows
    query_1min = create_aggregation_stream(spark, "1 minute", "sensor_aggregations_1min")
    print("✓ 1-minute aggregation stream started")

    query_5min = create_aggregation_stream(spark, "5 minutes", "sensor_aggregations_5min")
    print("✓ 5-minute aggregation stream started")

    query_1hour = create_aggregation_stream(spark, "1 hour", "sensor_aggregations_1hour")
    print("✓ 1-hour aggregation stream started")

    print("=" * 60)
    print("All aggregation streams running with tenant routing. Press Ctrl+C to stop.")
    print("=" * 60)

    # Wait for all queries
    try:
        query_1min.awaitTermination()
    except KeyboardInterrupt:
        print("\nStopping all streams...")
        query_1min.stop()
        query_5min.stop()
        query_1hour.stop()
        print("All streams stopped.")
