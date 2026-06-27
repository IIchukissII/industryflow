# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the streaming upsert's FK-resilience filter (ADR-0006). Pure; no pyspark/DB."""
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from upsert_filter import split_known_sensors  # noqa: E402


def _row(sensor_id, value=1.0):
    # (time, sensor_id, equipment_id, site_id, value, unit, quality_code)
    return ("2026-06-27T10:00:00Z", sensor_id, "eq", "site", value, "C", 0)


def test_keeps_known_drops_orphans():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    orphan = "11111111-1111-1111-1111-111111111111"
    rows = [_row(a), _row(orphan), _row(b), _row(orphan, 2.0)]
    kept, skipped = split_known_sensors(rows, {a, b})
    assert skipped == 2
    assert [r[1] for r in kept] == [a, b]


def test_uuid_objects_and_strings_match():
    sid = uuid.uuid4()
    # row carries a UUID object; valid set carries the string form (as read from the DB).
    kept, skipped = split_known_sensors([_row(sid)], {str(sid)})
    assert skipped == 0 and len(kept) == 1


def test_all_orphans_skipped_none_kept():
    kept, skipped = split_known_sensors([_row("x"), _row("y")], {str(uuid.uuid4())})
    assert kept == [] and skipped == 2


def test_empty_input():
    assert split_known_sensors([], {"a"}) == ([], 0)


def test_empty_valid_set_skips_all():
    kept, skipped = split_known_sensors([_row("a"), _row("b")], set())
    assert kept == [] and skipped == 2
