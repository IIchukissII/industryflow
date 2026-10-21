# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The aggregation job: rejected readings never reach the watermark, a tenant without a schema does
not fail every tenant's batch, and the windows are the ones the train/serve binding declares.

The watermark test runs a real streaming query on a local Spark over a file source: readings
arrive in files, one micro-batch each, and the query emits closed windows to a memory sink.
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

import aggregation_windows as aw
import kafka_aggregations as job
import reading_guard as rg
from reading_guard import company_id_to_schema, parse_readings

CID = "11111111-1111-1111-1111-111111111111"
SID = "aaaaaaaa-0000-0000-0000-000000000001"
EID = "eeeeeeee-0000-0000-0000-000000000001"


def _reading(ts, value, **extra):
    body = {"timestamp": ts.isoformat(), "sensor_id": SID, "equipment_id": EID, "site_id": "s",
            "company_id": CID, "value": value}
    body.update(extra)
    return body


# --- a future timestamp must not move the watermark ----------------------------------------------

def test_a_future_timestamp_does_not_close_live_windows(spark, tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    n = [0]

    def drop(readings):
        # One file per micro-batch: Kafka-shaped rows (value bytes + coordinates), as JSON lines.
        n[0] += 1
        with open(inbox / f"{n[0]:03d}.json", "w") as f:
            for i, r in enumerate(readings):
                f.write(json.dumps({"value": json.dumps(r), "topic": "t", "partition": 0,
                                    "offset": n[0] * 1000 + i}) + "\n")

    kafka_like = (spark.readStream.schema("value string, topic string, partition int, offset long")
                  .option("maxFilesPerTrigger", 1).json(str(inbox)))
    aggregated = job.aggregate_readings(parse_readings(kafka_like), "1min")
    query = (aggregated.writeStream.outputMode("append").format("memory")
             .queryName("agg_watermark").option("checkpointLocation", str(tmp_path / "ckpt"))
             .start())
    try:
        # Two live minutes of readings, well in the past of the processing clock.
        w0 = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(second=0, microsecond=0)
        drop([_reading(w0 + timedelta(seconds=s), 1.0) for s in (5, 15, 25)])
        # A device with a clock a year ahead. Unfiltered, it moves the watermark to next year.
        drop([_reading(w0 + timedelta(days=365), 999.0)])
        query.processAllAvailable()
        # More readings of the second minute arrive afterwards — on time by any honest clock.
        drop([_reading(w0 + timedelta(seconds=60 + s), 2.0) for s in (5, 15, 25)])
        query.processAllAvailable()
        # Legitimate later readings advance the watermark past both minutes, so both are emitted.
        drop([_reading(w0 + timedelta(minutes=10), 3.0)])
        query.processAllAvailable()
        drop([_reading(w0 + timedelta(minutes=11), 3.0)])
        query.processAllAvailable()
        deadline = time.time() + 30
        while time.time() < deadline:
            rows = spark.sql("SELECT * FROM agg_watermark").collect()
            if len(rows) >= 2:
                break
            query.processAllAvailable()
            time.sleep(0.5)
        # Collected timestamps are naive local time; astimezone reads them as such.
        by_end = {r["time"].astimezone(timezone.utc): r for r in rows}
    finally:
        query.stop()

    first, second = w0 + timedelta(minutes=1), w0 + timedelta(minutes=2)
    assert set(by_end) >= {first, second}
    second_row = by_end[second]
    # The second minute's readings were aggregated, not dropped as "late" behind a poisoned watermark.
    assert second_row["count_values"] == 3
    assert all(r["max_value"] < 999.0 for r in rows)


# --- one unknown tenant must not fail every tenant -----------------------------------------------

class _Conn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.autocommit = True

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        _Cur.conn = conn
        return _Cur()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return self._rows


def _agg_row(company_id, sensor_id=SID, equipment_id=EID):
    return {"time": datetime(2026, 1, 1, tzinfo=timezone.utc), "sensor_id": sensor_id,
            "equipment_id": equipment_id, "company_id": company_id, "avg_value": 1.0,
            "min_value": 1.0, "max_value": 1.0, "stddev_value": None, "count_values": 1,
            "anomaly_count": 0, "anomaly_percentage": 0.0}


def _known(*schemas, suspended=()):
    """A references reader for which only ``schemas`` are active tenants, each knowing SID and EID,
    and ``suspended`` are registered but inactive."""
    def references(cur, schema_name, sensor_ids, equipment_ids):
        if schema_name in suspended:
            return rg.SUSPENDED_TENANT, set(), set()
        if schema_name not in schemas:
            return rg.UNKNOWN_TENANT, set(), set()
        return None, {SID} & sensor_ids, {EID} & equipment_ids
    return references


def test_an_unknown_tenant_is_skipped_and_the_others_are_written():
    ghost = "99999999-9999-9999-9999-999999999999"
    other = "22222222-2222-2222-2222-222222222222"
    written = []
    conn = _Conn()
    job.write_aggregations_to_db(
        _Rows([_agg_row(ghost), _agg_row(CID), _agg_row(other)]), 0, "sensor_aggregations_1min",
        connect=lambda: conn,
        references=_known(company_id_to_schema(CID), company_id_to_schema(other)),
        upsert=lambda cur, target, values: written.append(target),
    )
    assert written == [f'"{company_id_to_schema(CID)}"."sensor_aggregations_1min"',
                       f'"{company_id_to_schema(other)}"."sensor_aggregations_1min"']
    assert conn.commits == 2  # one transaction per tenant


def test_a_suspended_tenant_is_skipped_like_the_streaming_job_rejects_it():
    # ADR-0033 dec 3: an inactive tenant's aggregates are not written, as its raw readings are not.
    other = "22222222-2222-2222-2222-222222222222"
    written = []
    job.write_aggregations_to_db(
        _Rows([_agg_row(CID), _agg_row(other)]), 0, "sensor_aggregations_1min",
        connect=_Conn,
        references=_known(company_id_to_schema(CID), suspended=(company_id_to_schema(other),)),
        upsert=lambda cur, target, values: written.append(target),
    )
    assert written == [f'"{company_id_to_schema(CID)}"."sensor_aggregations_1min"']


def test_an_unknown_sensor_or_equipment_is_excluded_like_the_streaming_job_rejects_it():
    # ADR-0006 dec 4: the aggregation excludes the readings the streaming job dead-letters,
    # by the same definition. A known tenant's aggregate for a sensor or equipment it does not know
    # is such a reading; only the known key is written.
    ghost_sensor = "aaaaaaaa-0000-0000-0000-00000000dead"
    ghost_equipment = "eeeeeeee-0000-0000-0000-00000000dead"
    written = []
    job.write_aggregations_to_db(
        _Rows([_agg_row(CID), _agg_row(CID, sensor_id=ghost_sensor),
               _agg_row(CID, equipment_id=ghost_equipment)]), 0, "sensor_aggregations_1min",
        connect=_Conn, references=_known(company_id_to_schema(CID)),
        upsert=lambda cur, target, values: written.extend(values),
    )
    assert [(v[1], v[2]) for v in written] == [(SID, EID)]


def test_a_tenant_whose_every_key_is_unknown_writes_nothing():
    conn = _Conn()
    job.write_aggregations_to_db(
        _Rows([_agg_row(CID, sensor_id="aaaaaaaa-0000-0000-0000-00000000dead")]), 0,
        "sensor_aggregations_1min", connect=lambda: conn,
        references=_known(company_id_to_schema(CID)),
        upsert=lambda cur, target, values: pytest.fail("nothing to write"),
    )
    assert conn.commits == 0


def test_a_write_failure_still_fails_the_batch():
    def boom(cur, target, values):
        raise RuntimeError("database unreachable")

    with pytest.raises(RuntimeError, match="database unreachable"):
        job.write_aggregations_to_db(_Rows([_agg_row(CID)]), 0, "sensor_aggregations_1min",
                                     connect=_Conn, references=_known(company_id_to_schema(CID)),
                                     upsert=boom)


# --- the windows are the declared binding --------------------------------------------------------

def test_every_aggregation_table_is_declared_once():
    assert sorted(aw.table_name(g) for g in aw.AGGREGATION_WINDOWS) == [
        "sensor_aggregations_1hour", "sensor_aggregations_1min", "sensor_aggregations_5min"]


@pytest.mark.parametrize("granularity,t,expected", [
    # 1-minute windows, 2 minutes allowed lateness: at 10:05:30 the newest written window is 10:02.
    ("1min", 10 * 3600 + 5 * 60 + 30, 10 * 60 + 2),
    # On a boundary: at 10:05:00 the watermark (10:03:00) has just reached the end of 10:02.
    ("1min", 10 * 3600 + 5 * 60, 10 * 60 + 2),
    # 1-hour windows, 15 minutes lateness: at 10:10 the 09:00 window is not yet written ...
    ("1hour", 10 * 3600 + 10 * 60, 8),
    # ... and at 10:20 it is.
    ("1hour", 10 * 3600 + 20 * 60, 9),
])
def test_latest_emitted_window(granularity, t, expected):
    assert aw.latest_emitted_window(t, granularity) == expected


# --- state partitions are part of the checkpoint (ADR-0006 dec 8) --------------------------------

def test_state_partitions_default_and_override(monkeypatch):
    monkeypatch.delenv("SPARK_SQL_SHUFFLE_PARTITIONS", raising=False)
    assert job.state_partitions() == job.DEFAULT_STATE_PARTITIONS
    monkeypatch.setenv("SPARK_SQL_SHUFFLE_PARTITIONS", "4")
    assert job.state_partitions() == 4
    monkeypatch.setenv("SPARK_SQL_SHUFFLE_PARTITIONS", "0")
    with pytest.raises(ValueError):
        job.state_partitions()


def test_a_changed_partition_count_gets_its_own_checkpoint():
    # Spark keeps the count a checkpoint was created with, so the same path for two counts would
    # leave a change silently ignored.
    a = job.checkpoint_path("s3a://ckpt", "sensor_aggregations_1min", 2)
    b = job.checkpoint_path("s3a://ckpt", "sensor_aggregations_1min", 8)
    assert a != b
    assert a == "s3a://ckpt/sensor_aggregations_1min-p2"
