# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Canonical tenant/partition naming for the cold layer (ADR-0025 decision 5, write side).

The object store carries the schema-per-tenant isolation the platform already fixed for the
database (ADR-0003). Both tenant mappings the exporter needs — `company_id -> object-store prefix`
and `company_id -> database schema` — are the platform's one tenant-naming module
(``industryflow_contracts.tenancy``, ADR-0003 dec 5, 7), which the notebook broker reads the same
prefix from. Both validate company_id as a UUID before building any name, so a malformed or
malicious id raises instead of producing an arbitrary prefix or SQL identifier. The exporter
derives the schema it reads and drops from the company_id; the registry's stored ``schema_name`` is
only checked against that derivation, never used on its own.
"""
from __future__ import annotations

from datetime import date

from industryflow_contracts import tenancy

# ``tenant_<uuid>`` (hyphenated): each tenant's Parquet lives under its own prefix — tenants are
# never pooled into a shared path keyed by a column (ADR-0025 dec 5).
company_id_to_prefix = tenancy.cold_object_prefix
# ``tenant_<uuid with underscores>``: the schema every service routes the tenant to.
company_id_to_schema = tenancy.schema_name


def day_partition(prefix: str, day: date) -> str:
    """Hive-style, time-partitioned directory for one day under a tenant prefix (ADR-0025 dec 6)."""
    return f"{prefix}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"


# The first part of a day keeps the name every export used before a day could hold several parts,
# so a day exported then is the same object a reader finds now.
FIRST_PART = "measurements.parquet"


def part_file(index: int) -> str:
    """File name of a day's ``index``-th Parquet part (0 = the day's first export)."""
    if index < 0:
        raise ValueError(f"part index must be >= 0, got {index}")
    return FIRST_PART if index == 0 else f"measurements-{index:04d}.parquet"


def part_key(prefix: str, day: date, part: str) -> str:
    """Object key of one Parquet part of a day."""
    if "/" in part or not part.endswith(".parquet"):
        raise ValueError(f"invalid part file name {part!r}")
    return f"{day_partition(prefix, day)}/{part}"


def parquet_key(prefix: str, day: date) -> str:
    """Object key of a day's first Parquet part."""
    return part_key(prefix, day, FIRST_PART)


def manifest_key(prefix: str, day: date) -> str:
    """
    Object key of a day's verification manifest.

    The manifest lists the day's verified Parquet parts with their row counts. A part is listed
    only after its write is verified and before the source rows it holds are dropped, so every
    listed part is a verified export (ADR-0025 dec 3, dec 4), and readers read exactly the listed
    parts.
    """
    return f"{day_partition(prefix, day)}/_manifest.json"
