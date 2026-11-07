# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The cold-layer purge against a real MinIO, as the job's own client and principal (ADR-0025 dec 14,
ADR-0033 dec 4, ADR-0026 dec 11).

The job sends batched DeleteObjects requests under its list-and-delete principal; a stand-in answers
them as it was written, not as the store does. Here the store answers: every object under the
tenant's prefix goes, across more than one batch; a longer sibling id, another tenant and a
non-tenant key stay; a principal the store refuses to delete for fails the step and leaves every
object in place.

Expects the compose MinIO with minio-init's principals: COLD_STORE_ENDPOINT, COLD_STORE_BUCKET,
MINIO_ACCESS_KEY / MINIO_SECRET_KEY (the root, to seed), TENANT_DELETE_COLD_* (the job's principal),
COLD_STORE_ACCESS_KEY / COLD_STORE_SECRET_KEY (the exporter's, which may not delete). Skips without
them, unless IF_REQUIRE_LIVE_STACK=1, which makes the skip a failure.
"""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tenant_delete.cold_store import ColdStore, client
from tenant_delete.config import ColdConfig

pytestmark = pytest.mark.integration

ENV = ("COLD_STORE_ENDPOINT", "COLD_STORE_BUCKET", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
       "TENANT_DELETE_COLD_ACCESS_KEY", "TENANT_DELETE_COLD_SECRET_KEY",
       "COLD_STORE_ACCESS_KEY", "COLD_STORE_SECRET_KEY")
_missing = [name for name in ENV if not os.getenv(name)]
if _missing:
    if os.getenv("IF_REQUIRE_LIVE_STACK") == "1":
        pytest.fail(f"IF_REQUIRE_LIVE_STACK=1 but {', '.join(_missing)} unset", pytrace=False)
    pytest.skip(f"no live MinIO ({', '.join(_missing)} unset)", allow_module_level=True)

BUCKET = os.environ["COLD_STORE_BUCKET"]
# More than one DeleteObjects request's worth (1000 keys).
OBJECTS = 1205


def _as(access_var: str, secret_var: str):
    return client(ColdConfig(endpoint=os.environ["COLD_STORE_ENDPOINT"], bucket=BUCKET,
                             region=os.getenv("COLD_STORE_REGION", "us-east-1"),
                             access_key=os.environ[access_var], secret_key=os.environ[secret_var]))


@pytest.fixture(scope="module")
def root():
    return _as("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")


def _keys(root, prefix: str) -> set:
    keys, token = set(), None
    while True:
        page = root.list_objects_v2(Bucket=BUCKET, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        keys |= {o["Key"] for o in page.get("Contents", [])}
        token = page.get("NextContinuationToken")
        if not token:
            return keys


def _put(root, keys):
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda k: root.put_object(Bucket=BUCKET, Key=k, Body=b"parquet"), keys))


@pytest.fixture
def tenants(root):
    """A tenant with its history, a longer sibling id, another tenant and a non-tenant key."""
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())
    seeded = {
        "mine": {f"tenant_{mine}/year=2026/month=01/day={i % 28 + 1:02d}/measurements-{i:04d}.parquet"
                 for i in range(OBJECTS)},
        "sibling": {f"tenant_{mine}0/year=2026/month=01/day=01/measurements-0000.parquet"},
        "other": {f"tenant_{other}/year=2026/month=01/day=01/measurements-0000.parquet"},
        "outside": {f"e2e-not-a-tenant-{mine}/x.parquet"},
    }
    _put(root, set().union(*seeded.values()))
    yield mine, seeded
    leftover = [k for keys in seeded.values() for k in keys]
    for i in range(0, len(leftover), 1000):
        root.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in leftover[i:i + 1000]], "Quiet": True})


def _present(root, seeded) -> dict:
    """What of each seeded group the store still holds, listed as the root sees it."""
    prefixes = {k.split("/", 1)[0] + "/" for keys in seeded.values() for k in keys}
    found = set().union(*(_keys(root, p) for p in prefixes))
    return {name: keys & found for name, keys in seeded.items()}


def test_every_object_under_the_tenant_goes_and_nothing_else(root, tenants):
    company_id, seeded = tenants
    removal = ColdStore(_as("TENANT_DELETE_COLD_ACCESS_KEY", "TENANT_DELETE_COLD_SECRET_KEY"), BUCKET).purge(company_id, [])
    assert removal.detail == {"bucket": BUCKET, "prefix": f"tenant_{company_id}/", "objects": OBJECTS}
    present = _present(root, seeded)
    assert present["mine"] == set(), f"{len(present['mine'])} object(s) of the deleted tenant remain"
    assert present["sibling"] == seeded["sibling"] and present["other"] == seeded["other"]
    assert present["outside"] == seeded["outside"]


def test_a_repeat_finds_nothing_and_removes_nothing(root, tenants):
    company_id, seeded = tenants
    store = ColdStore(_as("TENANT_DELETE_COLD_ACCESS_KEY", "TENANT_DELETE_COLD_SECRET_KEY"), BUCKET)
    store.purge(company_id, [])
    assert store.purge(company_id, []).detail["objects"] == 0
    assert _present(root, seeded)["other"] == seeded["other"]


def test_a_principal_the_store_refuses_to_delete_for_fails_the_step_and_keeps_every_object(root, tenants):
    # The exporter's principal lists the bucket but holds no delete verb (ADR-0025 dec 14). The store
    # answers a batch it refuses with per-key errors, not a failed request.
    company_id, seeded = tenants
    exporter = ColdStore(_as("COLD_STORE_ACCESS_KEY", "COLD_STORE_SECRET_KEY"), BUCKET)
    with pytest.raises(RuntimeError, match=r"refused \d+ deletion\(s\), first: .*AccessDenied"):
        exporter.purge(company_id, [])
    assert _present(root, seeded) == seeded, "a refused purge removed objects"


def test_the_job_principal_deletes_nothing_outside_a_tenant_prefix_in_a_batch(root, tenants):
    # The job's own request shape, a batched DeleteObjects, is held to the principal's key pattern.
    _, seeded = tenants
    job = _as("TENANT_DELETE_COLD_ACCESS_KEY", "TENANT_DELETE_COLD_SECRET_KEY")
    [outside] = seeded["outside"]
    errors = job.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": outside}], "Quiet": True}).get("Errors")
    assert [(e["Key"], e["Code"]) for e in errors or []] == [(outside, "AccessDenied")]
    assert _present(root, seeded)["outside"] == seeded["outside"]
