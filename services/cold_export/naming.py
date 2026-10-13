# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Canonical tenant/partition naming for the cold layer (ADR-0025 decision 5, write side).

The object store carries the schema-per-tenant isolation the platform already fixed for the
database (ADR-0003). This module is the exporter's *single* definition of both tenant mappings:
`company_id -> object-store prefix` and `company_id -> database schema` (ADR-0003 dec 5). Both
validate company_id as a UUID before building any name, so a malformed or malicious id raises
instead of producing an arbitrary prefix or SQL identifier. The exporter derives the schema it
reads and drops from the company_id; the registry's stored ``schema_name`` is only checked against
that derivation, never used on its own. There must be no second definition in this package.
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


def company_id_to_schema(company_id) -> str:
    """
    Map a company_id UUID to its tenant schema, e.g. ``tenant_<uuid with underscores>``.

    The same mapping every service applies (ADR-0003 dec 4, 5): the UUID is parsed first and only
    its canonical form reaches the identifier. A non-UUID raises ValueError.
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


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
