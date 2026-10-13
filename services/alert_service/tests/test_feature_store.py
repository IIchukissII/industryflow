# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The worker's latest-value snapshot store: tenant-scoped keys and the freshness bound.

Runs the in-memory backend, which shares the key scheme and the snapshot logic with Redis.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

from feature_store import FeatureStore  # noqa: E402

CO_A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
CO_B = "9d6c1c1e-3f8e-4a52-8f53-1d1f0b7c2a10"
EQ = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _store():
    return FeatureStore(use_memory=True)


def _at(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def test_key_names_the_tenant():
    assert FeatureStore._make_key(CO_A, EQ, "temp") == f"features:{CO_A}:{EQ}:temp"


def test_one_tenant_cannot_read_another_tenants_readings_for_the_same_equipment_id():
    fs = _store()
    asyncio.run(fs.store_reading(CO_A, EQ, "temp", _at(1), 42.0))

    snap_b, _ = asyncio.run(fs.get_current_snapshot(CO_B, EQ, ["temp"], now=NOW))
    snap_a, _ = asyncio.run(fs.get_current_snapshot(CO_A, EQ, ["temp"], now=NOW))

    assert snap_b == {}
    assert snap_a == {"temp": 42.0}


def test_key_canonicalises_ids_so_case_cannot_split_a_tenant():
    assert FeatureStore._make_key(CO_A.upper(), EQ, "t") == FeatureStore._make_key(CO_A, EQ, "t")


def test_stale_readings_are_left_out_of_the_snapshot():
    fs = _store()
    asyncio.run(fs.store_reading(CO_A, EQ, "temp", _at(10), 1.0))
    asyncio.run(fs.store_reading(CO_A, EQ, "pressure", _at(3600), 2.0))  # sensor went quiet an hour ago

    snap, as_of = asyncio.run(fs.get_current_snapshot(CO_A, EQ, ["temp", "pressure"],
                                                      max_age_seconds=300, now=NOW))

    assert snap == {"temp": 1.0}
    assert as_of == NOW - timedelta(seconds=10)


def test_unparseable_reading_time_counts_as_stale_when_bounded():
    fs = _store()
    asyncio.run(fs.store_reading(CO_A, EQ, "temp", "not-a-time", 1.0))
    snap, _ = asyncio.run(fs.get_current_snapshot(CO_A, EQ, ["temp"], max_age_seconds=300, now=NOW))
    assert snap == {}


def test_as_of_is_the_newest_reading_used():
    fs = _store()
    asyncio.run(fs.store_reading(CO_A, EQ, "a", _at(30), 1.0))
    asyncio.run(fs.store_reading(CO_A, EQ, "b", _at(5), 2.0))
    _, as_of = asyncio.run(fs.get_current_snapshot(CO_A, EQ, ["a", "b"], max_age_seconds=300, now=NOW))
    assert as_of == NOW - timedelta(seconds=5)
