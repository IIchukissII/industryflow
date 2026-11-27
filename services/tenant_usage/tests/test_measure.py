# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Each tenant is measured in both stores from the stores themselves (ADR-0049 dec 11): its database
bytes as the database reports them, its object bytes as the listing of its prefixes holds them, a key
counting only when the one ownership test gives it the tenant (ADR-0039)."""
import uuid

from industryflow_contracts import tenancy

from tenant_usage.measure import buckets, object_bytes, run

A, B = str(uuid.uuid4()), str(uuid.uuid4())
WHERE = buckets("industryflow-cold", "mlflow")


class Database:
    def __init__(self, sizes):
        self.sizes, self.recorded = sizes, []

    def tenant_database_bytes(self):
        return list(self.sizes.items())

    def record(self, company_id, store, size):
        self.recorded.append((company_id, store, size))


class Store:
    """A listing that, like a real store, answers by prefix alone: keys a prefix matches but the tenant
    does not own (a sibling, a key with a dot segment) are in it too."""

    def __init__(self, objects, fail_for=()):
        self.objects, self.fail_for = objects, set(fail_for)

    def list_objects(self, bucket, prefix):
        if any(prefix.endswith(tenancy.mlflow_artifact_prefix(cid)) or tenancy.cold_key_prefix(cid) == prefix
               for cid in self.fail_for):
            raise ConnectionError("store unreachable")
        return [(key, size) for (b, key), size in self.objects.items() if b == bucket and key.startswith(prefix)]


def test_a_tenant_is_measured_under_each_of_its_prefixes_and_only_what_it_owns():
    cold, art = tenancy.cold_key_prefix(A), tenancy.mlflow_artifact_prefix(A)
    store = Store({
        ("industryflow-cold", cold + "date=2026-01-01/part-0.parquet"): 100,
        ("mlflow", art + "5/run1/artifacts/model.skops"): 20,
        ("mlflow", art + "5/../escape"): 7,                     # not a plain key: not the tenant's
        ("mlflow", tenancy.mlflow_artifact_prefix(B) + "x"): 1000,  # another tenant's
        ("industryflow-cold", tenancy.cold_key_prefix(B) + "y"): 1000,
    })
    assert object_bytes(store, WHERE, A) == 120


def test_every_tenant_is_recorded_in_both_stores():
    db = Database({A: 5, B: 0})
    store = Store({("mlflow", tenancy.mlflow_artifact_prefix(B) + "m"): 9})
    assert run(db, store, WHERE) == 0
    assert sorted(db.recorded) == sorted([(A, "database", 5), (A, "object", 0), (B, "database", 0), (B, "object", 9)])


def test_a_tenant_whose_listing_fails_keeps_its_last_measurement_and_the_others_are_measured():
    db = Database({A: 5, B: 6})
    assert run(db, Store({}, fail_for={A}), WHERE) == 1
    assert (A, "object", 0) not in db.recorded
    assert (A, "database", 5) in db.recorded and (B, "object", 0) in db.recorded


def test_the_job_holds_no_write_or_delete_call():
    """It lists and records; nothing in it can write to or delete from the object store (dec 13)."""
    import inspect

    import tenant_usage.store as store_module

    source = inspect.getsource(store_module)
    assert "list_objects_v2" in source
    for call in ("put_object", "delete_object", "copy_object", "upload_"):
        assert call not in source


def test_a_bucket_the_deployment_does_not_run_is_left_out():
    assert [b.name for b in buckets(None, "mlflow")] == ["mlflow"]
    assert [b.name for b in buckets("cold", "")] == ["cold"]
