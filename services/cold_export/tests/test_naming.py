# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The single canonical company_id -> prefix and -> schema mappings (ADR-0025 dec 5, ADR-0003 dec 5)."""
import uuid
from datetime import date

import pytest

from cold_export.naming import (  # noqa: E402
    company_id_to_prefix, company_id_to_schema, manifest_key, parquet_key, part_file, part_key,
)


def test_prefix_is_canonical_uuid():
    cid = "0191E4A0-1111-2222-3333-444455556666"
    # Canonicalised to lowercase, hyphenated, under a tenant_ prefix — one definition, no column.
    assert company_id_to_prefix(cid) == f"tenant_{str(uuid.UUID(cid))}"


def test_prefix_rejects_non_uuid():
    # A malformed / injected id must raise, not produce an arbitrary object-store path.
    for bad in ["'; DROP", "../tenant_other", "not-a-uuid", ""]:
        with pytest.raises(ValueError):
            company_id_to_prefix(bad)


def test_partition_paths_are_time_partitioned():
    prefix = company_id_to_prefix(uuid.uuid4())
    day = date(2026, 3, 9)
    assert parquet_key(prefix, day) == f"{prefix}/year=2026/month=03/day=09/measurements.parquet"
    assert manifest_key(prefix, day) == f"{prefix}/year=2026/month=03/day=09/_manifest.json"


def test_distinct_tenants_never_share_a_prefix():
    a, b = company_id_to_prefix(uuid.uuid4()), company_id_to_prefix(uuid.uuid4())
    assert a != b
    assert not a.startswith(b) and not b.startswith(a)


def test_schema_is_the_canonical_underscored_uuid():
    cid = "0191E4A0-1111-2222-3333-444455556666"
    assert company_id_to_schema(cid) == "tenant_0191e4a0_1111_2222_3333_444455556666"


def test_schema_rejects_non_uuid():
    for bad in ['x"; DROP SCHEMA public CASCADE; --', "tenant_other", ""]:
        with pytest.raises(ValueError):
            company_id_to_schema(bad)


def test_part_files():
    prefix = company_id_to_prefix(uuid.uuid4())
    day = date(2026, 3, 9)
    # The first part keeps the single-file name, so days exported before parts existed still read.
    assert part_file(0) == "measurements.parquet"
    assert part_key(prefix, day, part_file(0)) == parquet_key(prefix, day)
    assert part_file(1) == "measurements-0001.parquet"
    for bad in ["../x.parquet", "a/b.parquet", "manifest.json"]:
        with pytest.raises(ValueError):
            part_key(prefix, day, bad)
