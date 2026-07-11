# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Canonical tenant/partition naming for the cold layer (ADR-0025 decision 5, write side).

The object store carries the schema-per-tenant isolation the platform already fixed for the
database (ADR-0003). This module is the *single* definition of the `company_id -> object-store
prefix` mapping — the object-store analogue of the streaming job's one `company_id_to_schema`
(`services/spark_jobs/kafka_to_timescaledb.py`). Like that function, it validates company_id as
a UUID before building any path, so a malformed or malicious id raises instead of producing an
arbitrary prefix (defence against path injection). There must be no second definition.
"""
from __future__ import annotations

import uuid
from datetime import date


def company_id_to_prefix(company_id) -> str:
    """
    Map a company_id UUID to its cold-store prefix, e.g. ``tenant_<uuid>``.

    company_id is validated as a UUID first; a non-UUID raises ValueError rather than yielding
    an arbitrary object-store path. Each tenant's Parquet lives under its own prefix — tenants
    are never pooled into a shared path keyed by a column (ADR-0025 dec 5, alternative F).
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical}"


def day_partition(prefix: str, day: date) -> str:
    """Hive-style, time-partitioned directory for one day under a tenant prefix (ADR-0025 dec 6)."""
    return f"{prefix}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"


def parquet_key(prefix: str, day: date) -> str:
    """Object key of a day's measurements Parquet file."""
    return f"{day_partition(prefix, day)}/measurements.parquet"


def manifest_key(prefix: str, day: date) -> str:
    """
    Object key of a day's verification manifest.

    The manifest's *presence* is the idempotency signal: it is written only after the Parquet
    write is verified and before the source chunk is dropped, so a manifest means "a verified
    export of this day exists" (ADR-0025 dec 3, dec 4).
    """
    return f"{day_partition(prefix, day)}/_manifest.json"
