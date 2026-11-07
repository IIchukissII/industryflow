# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The cold purge's loop guard: the one case a real store cannot be made to show.

Everything the store answers (batches, prefix scope, refusals, a repeat) is proved against MinIO in
test_cold_store_live.py (ADR-0026 dec 11).
"""
import pytest

from tenant_delete.cold_store import ColdStore

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


class _KeepsEverything:
    """Lists one object and reports every delete as done without removing it."""

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"Contents": [{"Key": f"{Prefix}x"}]}

    def delete_objects(self, Bucket, Delete):
        return {}


def test_a_store_that_keeps_what_it_reported_deleted_fails_rather_than_loops():
    with pytest.raises(RuntimeError, match="kept 1"):
        ColdStore(_KeepsEverything(), "b").purge(A, [])
