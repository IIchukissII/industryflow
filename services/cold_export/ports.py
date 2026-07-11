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
from typing import Iterator, Optional, Protocol

import pyarrow as pa


@dataclass(frozen=True)
class Tenant:
    """A tenant to export, resolved from the verified `public.companies` registry (ADR-0025 dec 5)."""

    company_id: str
    schema_name: str


@dataclass(frozen=True)
class WriteResult:
    """Outcome of writing one day's Parquet: the number of rows the writer actually emitted."""

    rows: int


class MeasurementSource(Protocol):
    """The hot store (TimescaleDB) as the exporter sees it: read raw, then drop the chunk."""

    def list_tenants(self) -> list[Tenant]:
        """Enumerate tenants from the trusted registry, not from any request (ADR-0025 dec 5)."""
        ...

    def exportable_days(self, schema_name: str, older_than: date) -> list[date]:
        """Distinct days (ascending) that hold measurements strictly older than ``older_than``."""
        ...

    def count_day(self, schema_name: str, day: date) -> int:
        """Authoritative source row count for one day — the number the export must reconcile to."""
        ...

    def read_day(self, schema_name: str, day: date) -> Iterator[pa.RecordBatch]:
        """Stream one day's rows ordered by ``equipment_id, time`` (ADR-0025 dec 6, within-file sort)."""
        ...

    def drop_day(self, schema_name: str, day: date) -> None:
        """Drop the day's hypertable chunk. Called ONLY after a verified export (ADR-0025 dec 3)."""
        ...


class ColdStore(Protocol):
    """The object store as the exporter sees it: write Parquet + manifest, verify by read-back."""

    def read_manifest(self, prefix: str, day: date) -> Optional[dict]:
        """Return the day's manifest, or None if no verified export exists yet (idempotency signal)."""
        ...

    def write_parquet(self, prefix: str, day: date, batches: Iterator[pa.RecordBatch]) -> WriteResult:
        """Write the day's measurements Parquet (partitioned by time, sorted within file)."""
        ...

    def parquet_row_count(self, prefix: str, day: date) -> int:
        """Row count from the *written* Parquet footer — read back for verification, not trusted."""
        ...

    def write_manifest(self, prefix: str, day: date, manifest: dict) -> None:
        """Write the verification manifest. Its presence marks the day as verifiably exported."""
        ...


class ExportError(RuntimeError):
    """A verification or write failure. Raised so the run fails BEFORE any chunk is dropped."""
