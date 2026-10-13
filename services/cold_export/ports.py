# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ports (interfaces) the cold-layer exporter depends on.

The orchestration (`exporter.py`) is written against these Protocols, not against psycopg2 or
an S3 client, so the export -> verify -> drop ordering (ADR-0025 dec 3) and the idempotency
logic (dec 4) can be unit-tested with in-memory fakes and never need live infrastructure. The
real adapters live in `source.py` (TimescaleDB) and `store.py` (object store).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import ContextManager, Iterator, Optional, Protocol

import pyarrow as pa


@dataclass(frozen=True)
class Tenant:
    """A tenant to export, as the `public.companies` registry lists it (ADR-0025 dec 5).

    ``schema_name`` is the registry's stored claim. The exporter does not use it: it derives the
    schema from ``company_id`` and refuses the tenant if the two disagree.
    """

    company_id: str
    schema_name: str


@dataclass(frozen=True)
class WriteResult:
    """Outcome of writing one Parquet part: the number of rows the writer actually emitted."""

    rows: int


class DaySnapshot(Protocol):
    """One day of a tenant's raw measurements, read from a single consistent database snapshot.

    ``rows`` and ``fingerprint`` describe exactly the rows `batches` streams. Rows may still change
    in the live table while the export runs; `drop` checks that they did not before it drops.
    """

    rows: int
    """Source row count of the day in the snapshot."""

    fingerprint: str
    """An order-independent digest of the day's rows in the snapshot (computed by the database)."""

    def batches(self) -> Iterator[pa.RecordBatch]:
        """Stream the day's rows ordered by ``equipment_id, time`` (ADR-0025 dec 6, within-file sort)."""
        ...

    def drop(self, part: str) -> bool:
        """Drop the day's rows and record that ``part`` completed the day, atomically.

        Under a lock on the day, the database re-reads the day's count and fingerprint; if either
        differs from the snapshot — a row arrived, changed or left after it was read — nothing is
        dropped and False is returned. Called ONLY after the part is verified and listed in the
        manifest (ADR-0025 dec 3).
        """
        ...


class MeasurementSource(Protocol):
    """The hot store (TimescaleDB) as the exporter sees it."""

    def exclusive_run(self) -> ContextManager[bool]:
        """Hold the one run-wide export lock for the context's duration; yields whether it was taken.

        Two overlapping runs could each see a day's last part as not yet written and write it, so
        one would overwrite a part the other's drop has already made immutable (ADR-0025 dec 4).
        The lock is never waited on: a run that cannot take it yields False and must do nothing.
        It is released when the context exits, or by the database if the holder dies.
        """
        ...

    def list_tenants(self) -> list[Tenant]:
        """Enumerate tenants from the trusted registry, not from any request (ADR-0025 dec 5)."""
        ...

    def exportable_days(self, schema_name: str, older_than: date) -> list[date]:
        """Distinct days (ascending) that hold measurements strictly older than ``older_than``."""
        ...

    def snapshot_day(self, schema_name: str, day: date) -> ContextManager[DaySnapshot]:
        """Open one day for export. Leaving the context without `drop` drops nothing."""
        ...

    def drop_recorded(self, schema_name: str, day: date, part: str) -> bool:
        """Whether a drop that completed ``part`` was committed (the database's own record)."""
        ...


class ColdStore(Protocol):
    """The object store as the exporter sees it: write Parquet parts + manifest, verify by read-back."""

    def read_manifest(self, prefix: str, day: date) -> Optional[dict]:
        """Return the day's manifest, or None if no verified export exists yet."""
        ...

    def write_part(self, prefix: str, day: date, part: str,
                   batches: Iterator[pa.RecordBatch]) -> WriteResult:
        """Write one Parquet part of the day (partitioned by time, sorted within the file)."""
        ...

    def part_row_count(self, prefix: str, day: date, part: str) -> int:
        """Row count from the *written* part's footer — read back for verification, not trusted."""
        ...

    def read_part_keys(self, prefix: str, day: date, part: str) -> pa.Table:
        """The natural key columns (``time``, ``sensor_id``) of one written part."""
        ...

    def write_manifest(self, prefix: str, day: date, manifest: dict) -> None:
        """Write the day's manifest: the list of verified parts readers read."""
        ...


class ExportError(RuntimeError):
    """A verification or write failure. Raised so the run fails BEFORE any chunk is dropped."""
