# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A deleted tenant's cold objects all go, and no other tenant's (ADR-0025 dec 14)."""
import pytest

from tenant_delete.cold_store import ColdStore

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


class FakeS3:
    def __init__(self, keys, refuse=()):
        self.keys = set(keys)
        self.refuse = set(refuse)
        self.batches = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        found = sorted(k for k in self.keys if k.startswith(Prefix))[:MaxKeys]
        return {"Contents": [{"Key": k} for k in found]} if found else {}

    def delete_objects(self, Bucket, Delete):
        keys = [o["Key"] for o in Delete["Objects"]]
        self.batches.append(len(keys))
        errors = [{"Key": k, "Code": "AccessDenied"} for k in keys if k in self.refuse]
        self.keys -= set(keys) - self.refuse
        return {"Errors": errors} if errors else {}


def test_every_object_under_the_tenant_prefix_goes_in_batches():
    mine = [f"tenant_{A}/year=2026/month=01/day={d:02d}/measurements-{i:04d}.parquet"
            for d in range(1, 29) for i in range(50)]
    sibling = f"tenant_{A}0/year=2026/x.parquet"  # a longer sibling id must not match
    other = "tenant_11111111-2222-3333-4444-555555555555/year=2026/x.parquet"
    s3 = FakeS3(mine + [sibling, other])
    removal = ColdStore(s3, "industryflow-cold").purge(A, [])
    assert s3.keys == {sibling, other}
    assert removal.detail == {"bucket": "industryflow-cold", "prefix": f"tenant_{A}/", "objects": len(mine)}
    assert max(s3.batches) <= 1000 and sum(s3.batches) == len(mine)


def test_a_refused_deletion_fails_the_step_so_it_is_not_recorded():
    key = f"tenant_{A}/year=2026/x.parquet"
    with pytest.raises(RuntimeError, match="refused 1"):
        ColdStore(FakeS3([key], refuse=[key]), "industryflow-cold").purge(A, [])


def test_nothing_left_is_an_empty_removal():
    assert ColdStore(FakeS3([]), "b").purge(A, []).detail["objects"] == 0


def test_a_store_that_keeps_what_it_reported_deleted_fails_rather_than_loops():
    class Sticky(FakeS3):
        def delete_objects(self, Bucket, Delete):
            return {}

    with pytest.raises(RuntimeError, match="kept 1"):
        ColdStore(Sticky([f"tenant_{A}/x"]), "b").purge(A, [])
