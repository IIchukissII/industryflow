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


def _agg_row(company_id):
    return {"time": datetime(2026, 1, 1, tzinfo=timezone.utc), "sensor_id": SID, "equipment_id": EID,
            "company_id": company_id, "avg_value": 1.0, "min_value": 1.0, "max_value": 1.0,
            "stddev_value": None, "count_values": 1, "anomaly_count": 0, "anomaly_percentage": 0.0}


def test_an_unknown_tenant_is_skipped_and_the_others_are_written():
    ghost = "99999999-9999-9999-9999-999999999999"
    other = "22222222-2222-2222-2222-222222222222"
    written = []
    conn = _Conn()
    job.write_aggregations_to_db(
        _Rows([_agg_row(ghost), _agg_row(CID), _agg_row(other)]), 0, "sensor_aggregations_1min",
        connect=lambda: conn,
        tenant_exists=lambda cur, schema: schema != company_id_to_schema(ghost),
        upsert=lambda cur, target, values: written.append(target),
    )
    assert written == [f'"{company_id_to_schema(CID)}"."sensor_aggregations_1min"',
                       f'"{company_id_to_schema(other)}"."sensor_aggregations_1min"']
    assert conn.commits == 2  # one transaction per tenant


def test_a_write_failure_still_fails_the_batch():
    def boom(cur, target, values):
        raise RuntimeError("database unreachable")

    with pytest.raises(RuntimeError, match="database unreachable"):
        job.write_aggregations_to_db(_Rows([_agg_row(CID)]), 0, "sensor_aggregations_1min",
                                     connect=_Conn, tenant_exists=lambda cur, schema: True, upsert=boom)


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
