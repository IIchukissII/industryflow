# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which Kafka readings the Spark jobs write, and where the others go.

A reading that can never be written — malformed, missing a required field, with an id that is not a
UUID, stamped further in the future than clocks can disagree, or naming a tenant, sensor or
equipment that does not exist — used to fail the whole micro-batch. Spark then retried the same
batch forever (ADR-0006 dec 4), so one bad reading halted ingestion for every tenant. Other such
readings were filtered out silently while the offset moved on. Both break ADR-0005 dec 3: a message
that stays unprocessable is routed to a dead-letter destination for inspection — never dropped while
its offset advances, and never allowed to wedge the stream.

So each reading is classified, and the ones that cannot be written are published to the dead-letter
topic, with the reason, before the batch's writes. A failure of the infrastructure itself (the
database or Kafka unreachable, a permission missing) is not a property of a reading: it still fails
the batch, so Spark retries it (ADR-0006 dec 4).

Two kinds of check, both here so the two jobs apply one definition:

  * stateless — decidable from the reading alone, expressed as a Spark column so the aggregation job
    can drop the same readings *before* its watermark (a far-future timestamp would otherwise drag
    the watermark forward and close every live window early);
  * referential — whether the tenant schema, the sensor and the equipment exist; decided on the
    driver from one lookup per tenant per batch (`classify_references`, pure).

The pure parts import nothing beyond the standard library; the Spark helpers import pyspark lazily.
"""
from __future__ import annotations

import uuid

# How far ahead of the processing clock an event timestamp may be. Ingestion rejects anything further
# ahead at the edge (services/ingestion_service/schemas.py carries the same bound; a test holds them
# equal); this is the stream's own guard for anything that reaches Kafka another way.
MAX_FUTURE_SKEW_SECONDS = 300

# Where unwritable readings go (ADR-0005 dec 3). Distinct from the alert worker's dead-letter topic:
# different consumer, different failure.
DEFAULT_DLQ_TOPIC = "sensor-data-raw.measurements.dlq"

# Rejection reasons, as published in the dead-letter envelope.
MISSING_OR_MALFORMED = "missing_or_malformed_field"
INVALID_COMPANY_ID = "invalid_company_id"
INVALID_DEVICE_ID = "invalid_sensor_or_equipment_id"
FUTURE_TIMESTAMP = "future_timestamp"
UNKNOWN_TENANT = "unknown_tenant"
UNKNOWN_SENSOR = "unknown_sensor"
UNKNOWN_EQUIPMENT = "unknown_equipment"

# The measurement columns are NOT NULL in the tenant DDL; company_id routes the row.
REQUIRED_FIELDS = ("time", "sensor_id", "equipment_id", "site_id", "company_id", "value")

UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# Kafka metadata kept on every parsed reading so a dead letter points at its source message.
SOURCE_COLUMNS = ("json_string", "kafka_topic", "kafka_partition", "kafka_offset")


def company_id_to_schema(company_id) -> str:
    """
    Convert a company_id UUID to its tenant schema name — the one definition both Spark jobs use.

    company_id arrives in untrusted Kafka payloads, so it is validated as a UUID before the
    schema/table name is built; a non-UUID raises ValueError instead of producing an arbitrary
    target (ADR-0003 dec 4, 5).
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


def canonical_uuid(value) -> str:
    """The canonical (lower-case, hyphenated) form, so a reading's id compares with the database's."""
    return str(uuid.UUID(str(value)))


def classify_references(keys, tenant_exists, known_sensors, known_equipment):
    """
    Decide, for each (company_id, sensor_id, equipment_id) a batch carries, whether it can be written.

    ``tenant_exists`` maps company_id -> bool; ``known_sensors`` / ``known_equipment`` map company_id
    -> the set of canonical ids present in that tenant's tables. Returns ``[(company_id, sensor_id,
    equipment_id, reason)]`` for the keys that cannot be written, with the ids exactly as the batch
    carried them (so they join back onto its rows). Keys the stateless check already passed are
    well-formed UUIDs.
    """
    rejected = []
    for company_id, sensor_id, equipment_id in keys:
        if not tenant_exists.get(company_id, False):
            reason = UNKNOWN_TENANT
        elif canonical_uuid(sensor_id) not in known_sensors.get(company_id, set()):
            reason = UNKNOWN_SENSOR
        elif canonical_uuid(equipment_id) not in known_equipment.get(company_id, set()):
            reason = UNKNOWN_EQUIPMENT
        else:
            continue
        rejected.append((company_id, sensor_id, equipment_id, reason))
    return rejected


# ------------------------------------------------------------------------------------ Spark helpers

def reading_schema():
    """The JSON shape of a reading on ``sensor-data-raw``.

    Every field is nullable here because ``from_json`` does not enforce nullability; required fields
    are enforced by `rejection_reason`, which dead-letters a reading that lacks one.
    """
    from pyspark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType

    return StructType([
        StructField("timestamp", StringType(), True),
        StructField("sensor_id", StringType(), True),
        StructField("equipment_id", StringType(), True),
        StructField("site_id", StringType(), True),
        StructField("company_id", StringType(), True),
        StructField("value", DoubleType(), True),
        StructField("unit", StringType(), True),
        StructField("quality_code", IntegerType(), True),
    ])


def parse_readings(kafka_df):
    """Parse a Kafka source frame into readings, keeping the raw message and its coordinates."""
    from pyspark.sql import functions as F

    return (
        kafka_df.select(
            F.col("value").cast("string").alias("json_string"),
            F.col("topic").alias("kafka_topic"),
            F.col("partition").alias("kafka_partition"),
            F.col("offset").alias("kafka_offset"),
        )
        .withColumn("data", F.from_json(F.col("json_string"), reading_schema()))
        .select(*SOURCE_COLUMNS, "data.*")
        # ISO-8601 with an offset. try_: under Spark 4's default ANSI mode a plain to_timestamp raises on
        # one malformed value and fails the whole batch; this yields null, which is rejected below.
        .withColumn("time", F.try_to_timestamp(F.col("timestamp")))
    )


def rejection_reason(max_future_skew_seconds: int = MAX_FUTURE_SKEW_SECONDS):
    """A column: why a parsed reading cannot be written, or null if it can (the stateless checks)."""
    from pyspark.sql import functions as F

    missing = None
    for field in REQUIRED_FIELDS:
        cond = F.col(field).isNull()
        missing = cond if missing is None else (missing | cond)
    bad_device = ~F.col("sensor_id").rlike(UUID_PATTERN) | ~F.col("equipment_id").rlike(UUID_PATTERN)
    horizon = F.expr(f"current_timestamp() + INTERVAL {int(max_future_skew_seconds)} SECONDS")
    return (
        F.when(missing, F.lit(MISSING_OR_MALFORMED))
        .when(~F.col("company_id").rlike(UUID_PATTERN), F.lit(INVALID_COMPANY_ID))
        .when(bad_device, F.lit(INVALID_DEVICE_ID))
        .when(F.col("time") > horizon, F.lit(FUTURE_TIMESTAMP))
    )


def dead_letter_records(rejected_df, consumer: str):
    """The Kafka records for rejected readings: key = company_id, value = a JSON envelope.

    The envelope carries the original message text, why it was rejected, which consumer rejected it,
    and the source coordinates — the same shape as the alert worker's dead letters plus the reason.
    ``rejected_df`` must have the parsed reading columns and ``reject_reason``.
    """
    from pyspark.sql import functions as F

    return rejected_df.select(
        F.col("company_id").cast("string").alias("key"),
        F.to_json(F.struct(
            F.col("json_string").alias("original"),
            F.col("reject_reason").alias("reason"),
            F.lit(consumer).alias("consumer"),
            F.col("kafka_topic").alias("topic"),
            F.col("kafka_partition").alias("partition"),
            F.col("kafka_offset").alias("offset"),
        )).alias("value"),
    )


def publish_dead_letters(rejected_df, consumer: str, bootstrap_servers: str, topic: str) -> None:
    """Publish rejected readings to the dead-letter topic; raises (failing the batch) if Kafka does.

    Acknowledged by all in-sync replicas (ADR-0005 dec 5): a dead letter is only as good as its
    durability, since the offset of the message it stands for moves on once the batch commits.
    """
    (
        dead_letter_records(rejected_df, consumer)
        .write.format("kafka")
        .option("kafka.bootstrap.servers", bootstrap_servers)
        .option("kafka.acks", "all")
        .option("topic", topic)
        .save()
    )
