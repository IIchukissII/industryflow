# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The single canonical company_id -> prefix mapping (ADR-0025 dec 5). Validation is the point."""
import uuid
from datetime import date

import pytest

from cold_export.naming import company_id_to_prefix, manifest_key, parquet_key  # noqa: E402


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
