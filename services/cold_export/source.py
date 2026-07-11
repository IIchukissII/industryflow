# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TimescaleDB adapter for the cold-layer exporter (ADR-0025).

Reads raw ``sensor_measurements`` a day at a time and drops the day's chunk once the export is
verified. Tenants come from the trusted ``public.companies`` registry — never from a request
(decision 5, write side). The chunk drop goes through a SECURITY DEFINER function so the
exporter's own role holds only SELECT + EXECUTE and never table ownership (least privilege; see
infrastructure/timescaledb/init-scripts/16-cold-layer-raw-retention.sql).
"""
from __future__ import annotations

import logging
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import psycopg2
import pyarrow as pa

from .config import DbConfig
from .ports import Tenant

logger = logging.getLogger(__name__)

# The raw columns exported to the cold layer — the full content of a measurement row. Ordered
# to match the Parquet schema below.
_COLUMNS = ["time", "sensor_id", "equipment_id", "site_id", "value", "unit", "quality_code", "is_anomaly"]

# Explicit Arrow schema so every day's file is byte-for-byte comparable and column pruning works
# on read. UUIDs are stored as strings (portable across analytical engines); time keeps its UTC
# instant.
COLD_SCHEMA = pa.schema([
    ("time", pa.timestamp("us", tz="UTC")),
    ("sensor_id", pa.string()),
    ("equipment_id", pa.string()),
    ("site_id", pa.string()),
    ("value", pa.float64()),
    ("unit", pa.string()),
    ("quality_code", pa.int32()),
    ("is_anomaly", pa.bool_()),
])

_READ_BATCH = 50_000


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


class PostgresMeasurementSource:
    """Concrete MeasurementSource over psycopg2 (see ports.MeasurementSource)."""

    def __init__(self, db: DbConfig):
        self._db = db

    def _connect(self):
        # closing() the connection (not `with conn:`, which only manages the transaction) is what
        # actually returns it to the server — the exporter role has a small connection limit.
        conn = psycopg2.connect(**self._db.dsn_kwargs())
        conn.autocommit = True
        return conn

    def list_tenants(self) -> list[Tenant]:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            # Verified identity: the registry the trusted job iterates, not a request value.
            cur.execute(
                "SELECT company_id, schema_name FROM public.companies "
                "WHERE schema_name IS NOT NULL ORDER BY company_id"
            )
            return [Tenant(company_id=str(cid), schema_name=schema) for cid, schema in cur.fetchall()]

    def exportable_days(self, schema_name: str, older_than: date) -> list[date]:
        cutoff, _ = _day_bounds(older_than)
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT DISTINCT (time AT TIME ZONE \'UTC\')::date AS d '
                f'FROM "{schema_name}".sensor_measurements WHERE time < %s ORDER BY d',
                (cutoff,),
            )
            return [row[0] for row in cur.fetchall()]

    def count_day(self, schema_name: str, day: date) -> int:
        start, end = _day_bounds(day)
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT count(*) FROM "{schema_name}".sensor_measurements '
                f'WHERE time >= %s AND time < %s',
                (start, end),
            )
            return int(cur.fetchone()[0])

    def read_day(self, schema_name: str, day: date) -> Iterator[pa.RecordBatch]:
        start, end = _day_bounds(day)
        conn = self._connect()
        # A server-side (named) cursor streams only inside a transaction — it cannot run under
        # autocommit. Turn autocommit off for this read so psycopg2 keeps a portal open and fetches
        # in itersize batches instead of materialising the whole day in the client.
        conn.autocommit = False
        # Named (server-side) cursor: stream the day rather than materialising it in the client.
        # ORDER BY equipment_id, time gives the within-file sort Parquet min/max stats exploit
        # (ADR-0025 dec 6).
        cur = conn.cursor(name=f"cold_read_{schema_name}_{day.isoformat()}")
        cur.itersize = _READ_BATCH
        try:
            cur.execute(
                f'SELECT {", ".join(_COLUMNS)} FROM "{schema_name}".sensor_measurements '
                f'WHERE time >= %s AND time < %s ORDER BY equipment_id, time',
                (start, end),
            )
            while True:
                rows = cur.fetchmany(_READ_BATCH)
                if not rows:
                    break
                yield _rows_to_batch(rows)
        finally:
            cur.close()
            conn.close()

    def drop_day(self, schema_name: str, day: date) -> None:
        start, end = _day_bounds(day)
        with closing(self._connect()) as conn, conn.cursor() as cur:
            # SECURITY DEFINER wrapper drops the day's chunk as the table owner; the exporter
            # role never owns the table. Called ONLY after a verified export (ADR-0025 dec 3).
            cur.execute("SELECT cold_export_drop_day(%s, %s, %s)", (schema_name, start, end))
            logger.info("Dropped exported chunk: %s %s", schema_name, day.isoformat())


def _rows_to_batch(rows) -> pa.RecordBatch:
    """Build one Arrow RecordBatch from psycopg2 tuples, column by column, cast to COLD_SCHEMA."""
    cols = list(zip(*rows)) if rows else [()] * len(_COLUMNS)
    return pa.record_batch(
        [
            pa.array(cols[0], type=COLD_SCHEMA.field("time").type),
            pa.array([None if v is None else str(v) for v in cols[1]], type=pa.string()),
            pa.array([None if v is None else str(v) for v in cols[2]], type=pa.string()),
            pa.array(cols[3], type=pa.string()),
            pa.array(cols[4], type=pa.float64()),
            pa.array(cols[5], type=pa.string()),
            pa.array(cols[6], type=pa.int32()),
            pa.array(cols[7], type=pa.bool_()),
        ],
        schema=COLD_SCHEMA,
    )
