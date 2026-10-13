# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TimescaleDB adapter for the cold-layer exporter (ADR-0025).

Reads raw ``sensor_measurements`` a day at a time and drops the day's chunk once the export is
verified. Tenants come from the trusted ``public.companies`` registry — never from a request
(decision 5, write side). The checked chunk drop goes through a SECURITY DEFINER function, so the
exporter's own role holds only SELECT + EXECUTE and never table ownership (least privilege; see
infrastructure/timescaledb/init-scripts/16-cold-layer-raw-retention.sql). Schema
names reach SQL only as quoted identifiers.
"""
from __future__ import annotations

import logging
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import psycopg2
import pyarrow as pa
from psycopg2 import sql

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

# The run-wide advisory lock key (two int4 halves, so it cannot collide with a single-bigint key
# another component takes). The first half namespaces the platform, the second the exporter.
RUN_LOCK_KEY = (0x49464C57, 0x434F4C44)  # "IFLW", "COLD"


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


class _PostgresDaySnapshot:
    """One day read inside a REPEATABLE READ transaction (see ports.DaySnapshot)."""

    def __init__(self, conn, schema_name: str, day: date, rows: int, fingerprint: str):
        self._conn = conn
        self._schema = schema_name
        self._day = day
        self.rows = rows
        self.fingerprint = fingerprint

    def batches(self) -> Iterator[pa.RecordBatch]:
        start, end = _day_bounds(self._day)
        # Named (server-side) cursor, in the snapshot the count and fingerprint were taken in: stream
        # the day rather than materialising it in the client. ORDER BY equipment_id, time gives the
        # within-file sort Parquet min/max stats exploit (ADR-0025 dec 6).
        cur = self._conn.cursor(name=f"cold_read_{self._schema}_{self._day.isoformat()}")
        cur.itersize = _READ_BATCH
        try:
            cur.execute(
                sql.SQL(
                    "SELECT {cols} FROM {schema}.sensor_measurements "
                    "WHERE time >= %s AND time < %s ORDER BY equipment_id, time"
                ).format(
                    cols=sql.SQL(", ").join(sql.Identifier(c) for c in _COLUMNS),
                    schema=sql.Identifier(self._schema),
                ),
                (start, end),
            )
            while True:
                rows = cur.fetchmany(_READ_BATCH)
                if not rows:
                    break
                yield _rows_to_batch(rows)
        finally:
            cur.close()

    def drop(self, part: str) -> bool:
        start, end = _day_bounds(self._day)
        # End the read snapshot; the drop is its own short READ COMMITTED transaction, so the
        # re-read inside it sees every row committed up to the lock — a REPEATABLE READ snapshot
        # taken before the lock would not.
        self._conn.rollback()
        self._conn.set_session(isolation_level=psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED,
                               readonly=False)
        with self._conn.cursor() as cur:
            # SECURITY DEFINER: locks the day's chunks, re-reads the day's count and fingerprint, and
            # only if both still equal the snapshot's drops the chunk as the table owner and records
            # the drop — this commit makes both durable at once (ADR-0025 dec 3).
            cur.execute(
                "SELECT cold_export_drop_day(%s, %s, %s, %s, %s, %s)",
                (self._schema, start, end, self.rows, self.fingerprint, part),
            )
            dropped = bool(cur.fetchone()[0])
        self._conn.commit()
        if dropped:
            logger.info("Dropped exported chunk: %s %s (%s)", self._schema, self._day.isoformat(), part)
        return dropped


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

    @contextmanager
    def exclusive_run(self) -> Iterator[bool]:
        # A session-level advisory lock on its own connection, held for the whole run. It is not a
        # transaction lock, so the per-day snapshot and drop transactions on other connections do
        # not release it; if this process dies the server ends the session and frees the lock, so a
        # crashed run never blocks the next one.
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s, %s)", RUN_LOCK_KEY)
                acquired = bool(cur.fetchone()[0])
            yield acquired
            if acquired:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s, %s)", RUN_LOCK_KEY)
        finally:
            conn.close()

    def list_tenants(self) -> list[Tenant]:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            # Verified identity: the registry the trusted job iterates, not a request value. The
            # stored schema_name is returned only to be checked against the canonical derivation.
            cur.execute(
                "SELECT company_id, schema_name FROM public.companies "
                "WHERE schema_name IS NOT NULL ORDER BY company_id"
            )
            return [Tenant(company_id=str(cid), schema_name=schema) for cid, schema in cur.fetchall()]

    def exportable_days(self, schema_name: str, older_than: date) -> list[date]:
        cutoff, _ = _day_bounds(older_than)
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT DISTINCT (time AT TIME ZONE 'UTC')::date AS d "
                    "FROM {schema}.sensor_measurements WHERE time < %s ORDER BY d"
                ).format(schema=sql.Identifier(schema_name)),
                (cutoff,),
            )
            return [row[0] for row in cur.fetchall()]

    @contextmanager
    def snapshot_day(self, schema_name: str, day: date) -> Iterator[_PostgresDaySnapshot]:
        start, end = _day_bounds(day)
        conn = psycopg2.connect(**self._db.dsn_kwargs())
        # REPEATABLE READ: the count, the fingerprint and the streamed rows all come from one
        # snapshot, so they describe the same rows. (A server-side cursor needs a transaction anyway.)
        conn.set_session(isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ,
                         readonly=True, autocommit=False)
        try:
            with conn.cursor() as cur:
                # The same database function the drop re-runs under its lock, so the two digests are
                # computed identically.
                cur.execute("SELECT rows, fingerprint FROM public.cold_export_day_fingerprint(%s, %s, %s)",
                            (schema_name, start, end))
                rows, fingerprint = cur.fetchone()
            yield _PostgresDaySnapshot(conn, schema_name, day, int(rows), str(fingerprint))
        finally:
            # Without a drop the snapshot transaction is still open: end it. After a drop it has
            # committed and this is a no-op.
            conn.rollback()
            conn.close()

    def drop_recorded(self, schema_name: str, day: date, part: str) -> bool:
        with closing(self._connect()) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM public.cold_export_drops "
                "WHERE schema_name = %s AND day = %s AND part = %s)",
                (schema_name, day, part),
            )
            return bool(cur.fetchone()[0])


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
