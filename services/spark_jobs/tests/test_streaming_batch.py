# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Micro-batch routing of the raw-measurement job (ADR-0005 dec 3, ADR-0006 dec 4).

One unwritable reading used to fail the whole batch — every tenant's — and Spark replayed it forever;
others were dropped silently while the offset advanced. These drive `process_batch` on a local Spark
with its database lookups, dead-letter publisher and writer injected: the writable rows of every
tenant are written, each unwritable one is dead-lettered with its reason, and an infrastructure
failure still fails the batch.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import kafka_to_timescaledb as job
import reading_guard as rg

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
GHOST = "99999999-9999-9999-9999-999999999999"
S_A, E_A = "aaaaaaaa-0000-0000-0000-00000000000a", "eeeeeeee-0000-0000-0000-00000000000a"
S_B, E_B = "aaaaaaaa-0000-0000-0000-00000000000b", "eeeeeeee-0000-0000-0000-00000000000b"
ORPHAN_S = "aaaaaaaa-0000-0000-0000-0000000dead0"
ORPHAN_E = "eeeeeeee-0000-0000-0000-0000000dead0"


def msg(company, sensor, equipment, value, timestamp=None, **extra):
    body = {"timestamp": timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "sensor_id": sensor, "equipment_id": equipment, "site_id": "site-1",
            "company_id": company, "value": value}
    body.update(extra)
    return {k: v for k, v in body.items() if v is not None}


def batch(spark, messages):
    rows = [(json.dumps(m).encode(), "sensor-data-raw", 0, i) for i, m in enumerate(messages)]
    kafka_df = spark.createDataFrame(rows, "value binary, topic string, partition int, offset long")
    return job.build_readings(kafka_df)


class Sinks:
    def __init__(self, tenants=(A, B), sensors=None, equipment=None):
        self.tenants = set(tenants)
        self.sensors = sensors or {A: {S_A}, B: {S_B}}
        self.equipment = equipment or {A: {E_A}, B: {E_B}}
        self.dead = []
        self.written = {}
        self.events = []
        self.fail_upsert_for = None
        self.fail_dead_letter = False

    def lookup(self, keys):
        companies = {k[0] for k in keys}
        return ({c: c in self.tenants for c in companies},
                {c: self.sensors.get(c, set()) for c in companies},
                {c: self.equipment.get(c, set()) for c in companies})

    def dead_letter(self, df):
        if self.fail_dead_letter:
            raise RuntimeError("kafka unreachable")
        self.events.append("dead_letter")
        self.dead.extend((r["value"], r["reject_reason"]) for r in df.collect())

    def upsert(self, df, schema):
        self.events.append(f"upsert:{schema}")
        if schema == self.fail_upsert_for:
            raise RuntimeError("database unreachable")
        self.written.setdefault(schema, []).extend(r["value"] for r in df.collect())

    def run(self, df):
        job.process_batch(df, 0, lookup=self.lookup, dead_letter=self.dead_letter, upsert=self.upsert)


def test_unwritable_readings_are_dead_lettered_and_every_tenant_still_writes(spark):
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    sinks = Sinks()
    sinks.run(batch(spark, [
        msg(A, S_A, E_A, 1.0),
        msg(A, ORPHAN_S, E_A, 2.0),           # sensor not provisioned (the old FK filter's case)
        msg(A, S_A, ORPHAN_E, 3.0),           # equipment not provisioned (failed the batch before)
        msg(B, S_B, E_B, 4.0),
        msg(GHOST, S_B, E_B, 5.0),            # tenant without a schema (UndefinedTable before)
        msg(A, S_A, E_A, 6.0, timestamp=future),
        msg(None, S_A, E_A, 7.0),             # no company_id (dropped silently before)
        msg(A, "s-1", E_A, 8.0),              # not a UUID (failed the INSERT before)
    ]))

    assert sinks.written == {rg.company_id_to_schema(A): [1.0], rg.company_id_to_schema(B): [4.0]}
    assert sorted(sinks.dead) == sorted([
        (2.0, rg.UNKNOWN_SENSOR), (3.0, rg.UNKNOWN_EQUIPMENT), (5.0, rg.UNKNOWN_TENANT),
        (6.0, rg.FUTURE_TIMESTAMP), (7.0, rg.MISSING_OR_MALFORMED), (8.0, rg.INVALID_DEVICE_ID),
    ])
    # Dead letters go out before any write: if publishing them fails, nothing has moved.
    assert sinks.events[0] == "dead_letter"


def test_a_clean_batch_publishes_no_dead_letters(spark):
    sinks = Sinks()
    sinks.run(batch(spark, [msg(A, S_A, E_A, 1.0), msg(B, S_B, E_B, 2.0)]))
    assert sinks.dead == [] and "dead_letter" not in sinks.events
    assert sum(len(v) for v in sinks.written.values()) == 2


def test_an_infrastructure_failure_still_fails_the_batch(spark):
    sinks = Sinks()
    sinks.fail_upsert_for = rg.company_id_to_schema(B)
    with pytest.raises(RuntimeError, match="database unreachable"):
        sinks.run(batch(spark, [msg(A, S_A, E_A, 1.0), msg(B, S_B, E_B, 2.0)]))


def test_a_dead_letter_failure_fails_the_batch_before_any_write(spark):
    sinks = Sinks()
    sinks.fail_dead_letter = True
    with pytest.raises(RuntimeError, match="kafka unreachable"):
        sinks.run(batch(spark, [msg(A, S_A, E_A, 1.0), msg(A, ORPHAN_S, E_A, 2.0)]))
    assert sinks.written == {}


def test_the_dead_letter_envelope_names_its_source(spark):
    df = batch(spark, [msg(A, ORPHAN_S, E_A, 2.0)])
    rejected = []
    job.process_batch(df, 7, lookup=Sinks().lookup, dead_letter=lambda d: rejected.extend(
        rg.dead_letter_records(d, job.DLQ_CONSUMER).collect()), upsert=lambda d, s: None)
    [record] = rejected
    envelope = json.loads(record["value"])
    assert envelope["reason"] == rg.UNKNOWN_SENSOR
    assert envelope["consumer"] == "spark-streaming"
    assert json.loads(envelope["original"])["sensor_id"] == ORPHAN_S
