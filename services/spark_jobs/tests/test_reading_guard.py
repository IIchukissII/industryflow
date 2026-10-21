# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The reading guard (ADR-0005 dec 3, ADR-0006 dec 4): which readings are writable, and the dead-letter
envelope for the rest. Stateless checks run on a local Spark over Kafka-shaped rows; the referential
classification is pure.
"""
import ast
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import reading_guard as rg

CID = "11111111-2222-3333-4444-555555555555"
SID = "aaaaaaaa-0000-0000-0000-000000000001"
EID = "bbbbbbbb-0000-0000-0000-000000000001"
SUSPENDED = "33333333-2222-3333-4444-555555555555"
UNMAPPED = "44444444-2222-3333-4444-555555555555"


def reading(**overrides):
    body = {
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "sensor_id": SID, "equipment_id": EID, "site_id": "site-1", "company_id": CID,
        "value": 1.5, "unit": "C", "quality_code": 0,
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


def kafka_frame(spark, messages):
    """A frame shaped like the Kafka source: value bytes + topic/partition/offset."""
    rows = [(m if isinstance(m, (bytes, bytearray)) else json.dumps(m).encode(), "sensor-data-raw", 0, i)
            for i, m in enumerate(messages)]
    return spark.createDataFrame(rows, "value binary, topic string, partition int, offset long")


def reasons(spark, messages):
    parsed = rg.parse_readings(kafka_frame(spark, messages))
    return [r["r"] for r in parsed.withColumn("r", rg.rejection_reason()).orderBy("kafka_offset").collect()]


# --- stateless checks ---------------------------------------------------------------------------

def test_a_well_formed_reading_is_writable(spark):
    assert reasons(spark, [reading()]) == [None]


@pytest.mark.parametrize("field", ["timestamp", "sensor_id", "equipment_id", "site_id", "company_id", "value"])
def test_a_missing_required_field_is_rejected(spark, field):
    # from_json does not enforce nullability; the guard does.
    assert reasons(spark, [reading(**{field: None})]) == [rg.MISSING_OR_MALFORMED]


def test_malformed_json_and_an_unparseable_timestamp_are_rejected(spark):
    assert reasons(spark, [b"{not json", reading(timestamp="yesterday-ish")]) == \
        [rg.MISSING_OR_MALFORMED, rg.MISSING_OR_MALFORMED]


def test_ids_that_are_not_uuids_are_rejected(spark):
    # A non-UUID would fail the INSERT into a UUID column and, with it, the whole batch.
    assert reasons(spark, [reading(company_id="acme"), reading(sensor_id="s-1"),
                           reading(equipment_id="pump 7")]) == \
        [rg.INVALID_COMPANY_ID, rg.INVALID_DEVICE_ID, rg.INVALID_DEVICE_ID]


def test_a_timestamp_beyond_the_clock_skew_bound_is_rejected(spark):
    now = datetime.now(timezone.utc)
    near = (now + timedelta(seconds=rg.MAX_FUTURE_SKEW_SECONDS - 60)).isoformat()
    far = (now + timedelta(days=365)).isoformat()
    assert reasons(spark, [reading(timestamp=near), reading(timestamp=far)]) == [None, rg.FUTURE_TIMESTAMP]


# --- referential checks (pure) ------------------------------------------------------------------

def test_classify_references():
    other = "99999999-2222-3333-4444-555555555555"
    orphan_sensor = "aaaaaaaa-0000-0000-0000-00000000dead"
    orphan_equipment = "bbbbbbbb-0000-0000-0000-00000000dead"
    keys = [(CID, SID, EID), (CID, orphan_sensor, EID), (CID, SID, orphan_equipment),
            (other, SID, EID), (CID, SID.upper(), EID.upper()), (SUSPENDED, SID, EID),
            (UNMAPPED, SID, EID)]
    rejected = rg.classify_references(
        keys,
        tenant_refusal={CID: None, other: rg.UNKNOWN_TENANT, SUSPENDED: rg.SUSPENDED_TENANT},
        known_sensors={CID: {SID}},
        known_equipment={CID: {EID}},
    )
    assert rejected == [
        (CID, orphan_sensor, EID, rg.UNKNOWN_SENSOR),
        (CID, SID, orphan_equipment, rg.UNKNOWN_EQUIPMENT),
        (other, SID, EID, rg.UNKNOWN_TENANT),
        (SUSPENDED, SID, EID, rg.SUSPENDED_TENANT),
        (UNMAPPED, SID, EID, rg.UNKNOWN_TENANT),
    ]


class _Cursor:
    """Answers the reads `read_tenant_references` makes, in order, from canned rows."""

    def __init__(self, registry, table_exists=True):
        self.answers = [registry, (table_exists,), None, None]
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchone(self):
        return self.answers[len(self.queries) - 1]

    def fetchall(self):
        return [(SID,)] if "sensors" in self.queries[-1] else [(EID,)]


@pytest.mark.parametrize("registry,table_exists,refusal", [
    (None, True, rg.UNKNOWN_TENANT),        # a schema the registry does not hold fails closed
    ((False,), True, rg.SUSPENDED_TENANT),  # ADR-0033 dec 3
    ((True,), False, rg.UNKNOWN_TENANT),    # registered, but its schema is gone
    ((True,), True, None),
])
def test_read_tenant_references_refuses_from_the_registry(registry, table_exists, refusal):
    cur = _Cursor(registry, table_exists)
    got, sensors, equipment = rg.read_tenant_references(
        cur, rg.company_id_to_schema(CID), {SID}, {EID})
    assert got == refusal
    assert "public.companies" in cur.queries[0]
    assert (sensors, equipment) == (({SID}, {EID}) if refusal is None else (set(), set()))


def test_company_id_to_schema_validates():
    assert rg.company_id_to_schema(CID.upper()) == "tenant_11111111_2222_3333_4444_555555555555"
    with pytest.raises(ValueError):
        rg.company_id_to_schema('x"; DROP SCHEMA public; --')


# --- the dead-letter envelope -------------------------------------------------------------------

def test_dead_letter_records_carry_the_original_and_the_reason(spark):
    msg = reading(sensor_id="not-a-uuid")
    parsed = rg.parse_readings(kafka_frame(spark, [msg])).withColumn("reject_reason", rg.rejection_reason())
    [record] = rg.dead_letter_records(parsed, "spark-streaming").collect()
    envelope = json.loads(record["value"])
    assert record["key"] == CID
    assert json.loads(envelope["original"]) == msg
    assert envelope["reason"] == rg.INVALID_DEVICE_ID
    assert envelope["consumer"] == "spark-streaming"
    assert (envelope["topic"], envelope["partition"], envelope["offset"]) == ("sensor-data-raw", 0, 0)


# --- one bound, two places ----------------------------------------------------------------------

def test_ingestion_and_stream_use_the_same_future_skew_bound():
    """Ingestion rejects at the edge what the stream would dead-letter; the bounds must agree, or a
    reading accepted at the edge could be dead-lettered in the stream."""
    schemas = Path(__file__).resolve().parents[2] / "ingestion_service" / "schemas.py"
    tree = ast.parse(schemas.read_text())
    values = [node.value.value for node in ast.walk(tree)
              if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
              and any(isinstance(t, ast.Name) and t.id == "MAX_FUTURE_SKEW_SECONDS" for t in node.targets)]
    assert values == [rg.MAX_FUTURE_SKEW_SECONDS]


def test_uuid_pattern_accepts_what_the_database_does():
    assert uuid.UUID(SID)
    import re
    assert re.match(rg.UUID_PATTERN, SID) and re.match(rg.UUID_PATTERN, SID.upper())
    assert not re.match(rg.UUID_PATTERN, SID + "0")
