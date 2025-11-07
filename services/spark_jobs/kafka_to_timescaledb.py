"""
Spark Structured Streaming Job: Kafka to TimescaleDB
Reads sensor data from Kafka and writes to TimescaleDB in real-time
Schema-per-tenant architecture: Routes data to tenant_<uuid>.sensor_measurements
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import from_json, col, substring, to_timestamp
from pyspark.sql.types import StructType, StructField, StringType, DoubleType, IntegerType
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_spark_session(app_name="IndustryFlow-Streaming"):
    """Create and configure Spark session with Kafka support"""
    spark = SparkSession.builder \
        .appName(app_name) \
        .master(os.getenv("SPARK_MASTER", "local[*]")) \
        .config("spark.jars.packages",
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
                "org.postgresql:postgresql:42.6.0") \
        .config("spark.sql.streaming.checkpointLocation",
                os.getenv("CHECKPOINT_LOCATION", "/tmp/spark-checkpoint")) \
        .config("spark.streaming.stopGracefullyOnShutdown", "true") \
        .getOrCreate()

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
    """Convert company_id UUID to schema name"""
    # Replace hyphens with underscores: 550e8400-e29b-41d4-a716-446655440000 -> 550e8400_e29b_41d4_a716_446655440000
    schema_uuid = company_id.replace("-", "_")
    return f"tenant_{schema_uuid}"


def write_to_timescaledb(batch_df, batch_id):
    """
    Write each micro-batch to TimescaleDB with schema-per-tenant routing
    Groups by company_id and writes to respective tenant schemas
    """
    if batch_df.count() > 0:
        logger.info(f"Processing batch {batch_id} with {batch_df.count()} rows")

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
            "numPartitions": "12",
            "stringtype": "unspecified"
        }

        # Group by company_id and write to respective tenant schemas
        company_ids = batch_df.select("company_id").distinct().collect()
        
        for row in company_ids:
            company_id = row["company_id"]
            if not company_id:
                logger.warning(f"Batch {batch_id}: Skipping rows with null company_id")
                continue
            
            # Filter data for this company
            tenant_data = batch_df.filter(col("company_id") == company_id)
            row_count = tenant_data.count()
            
            # Determine target schema
            schema_name = company_id_to_schema(company_id)
            table_name = f"{schema_name}.sensor_measurements"
            
            logger.info(f"Batch {batch_id}: Writing {row_count} rows to {table_name}")
            
            try:
                # Write to tenant-specific schema
                tenant_data.write \
                    .jdbc(
                        url=jdbc_url,
                        table=table_name,
                        mode="append",
                        properties=db_properties
                    )
                
                logger.info(f"Batch {batch_id}: Successfully wrote to {table_name}")
                
            except Exception as e:
                logger.error(f"Batch {batch_id}: Failed to write to {table_name}: {str(e)}")
                raise

        logger.info(f"Batch {batch_id} processing complete")
    else:
        logger.info(f"Batch {batch_id} is empty, skipping write")


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
        .option("startingOffsets", "latest") \
        .option("maxOffsetsPerTrigger", "10000") \
        .option("kafka.max.partition.fetch.bytes", "1048576") \
        .load()

    logger.info("Successfully connected to Kafka")

    # Parse JSON data and transform
    parsed_stream = raw_stream \
        .selectExpr("CAST(value AS STRING) as json_string") \
        .select(from_json(col("json_string"), sensor_schema).alias("data")) \
        .select("data.*") \
        .withColumnRenamed("timestamp", "time_str") \
        .withColumn("time",
                   to_timestamp(substring(col("time_str"), 1, 19), "yyyy-MM-dd'T'HH:mm:ss")) \
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

    # Write to TimescaleDB using foreachBatch with tenant routing
    checkpoint_location = os.getenv("CHECKPOINT_LOCATION", "/tmp/spark-checkpoint-timescale")

    query = parsed_stream \
        .writeStream \
        .outputMode("append") \
        .foreachBatch(write_to_timescaledb) \
        .trigger(processingTime="2 seconds") \
        .option("checkpointInterval", "2 seconds") \
        .option("spark.sql.streaming.minBatchesToRetain", "2") \
        .option("checkpointLocation", checkpoint_location) \
        .start()

    logger.info("Streaming query started with tenant routing. Waiting for data...")

    # Wait for termination
    query.awaitTermination()


if __name__ == "__main__":
    main()
