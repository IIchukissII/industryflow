# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
End-to-end proof for the cold-store broker (ADR-0025 dec 5, read side).

Every other broker test uses a fake store + object store; this one drives the REAL path against a
live stack: a running broker, Redis (the capability store), and the object store (MinIO). It
stands up nothing itself — it expects a live notebooks-profile stack reachable via env — and proves
what the fakes cannot:

  * a valid cold handle LISTS its own tenant's objects (relative paths) and READS an object's bytes
    through a real 307 -> pre-signed GET;
  * a handle for another tenant sees an EMPTY listing and gets NoSuchKey for the first tenant's key
    (cross-tenant isolation carried into the object store);
  * a wrong-audience handle and a revoked handle are both refused (401).

IMPORTANT: this must run from INSIDE the compose network (a container on
``industryflow-network``), because the broker's 307 points at the object store's endpoint
(``minio:9000``) — a host-side runner could not follow the redirect. See cold-layer-integration.yml.

When the stack is not reachable it SKIPS — so it never fails a driver-free run; the
IF_REQUIRE_LIVE_STACK guard (conftest.py) turns that skip into a failure in the CI job that DOES
stand up the stack.
"""
import io
import json
import os
import sys
import uuid

import pytest

requests = pytest.importorskip("requests")
redis = pytest.importorskip("redis")
boto3 = pytest.importorskip("boto3")
from botocore.client import Config  # noqa: E402

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import policy  # noqa: E402  — reuse the broker's own prefix derivation, don't reinvent it

pytestmark = pytest.mark.integration

_KEY_PREFIX = "nbcap:"
_PARQUET_MAGIC = b"PAR1"


def _broker_url():
    return os.getenv("COLD_BROKER_URL", "http://notebook-cold-broker:5060").rstrip("/")


def _redis_client():
    return redis.Redis.from_url(os.getenv("COLD_BROKER_REDIS_URL", "redis://redis:6379/0"))


def _s3():
    """A client with WRITE creds, used only to SEED objects (the broker itself is read-only)."""
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("COLD_STORE_ENDPOINT", "http://minio:9000"),
        aws_access_key_id=os.getenv("COLD_SEED_ACCESS_KEY", os.getenv("COLD_STORE_ACCESS_KEY")),
        aws_secret_access_key=os.getenv("COLD_SEED_SECRET_KEY", os.getenv("COLD_STORE_SECRET_KEY")),
        region_name=os.getenv("COLD_STORE_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _mint(rc, *, company_id, audience="cold", user="e2e-tester"):
    handle = "e2e-" + uuid.uuid4().hex
    record = {"user": user, "company_id": company_id, "audience": audience, "read_only": True}
    rc.set(f"{_KEY_PREFIX}{handle}", json.dumps(record), ex=300)
    return handle


@pytest.fixture(scope="module")
def stack():
    """Two tenants (A seeded with one object) + a Redis handle, or a skip if unreachable."""
    bucket = os.getenv("COLD_STORE_BUCKET", "industryflow-cold")
    try:
        rc = _redis_client(); rc.ping()
        s3 = _s3()
        # A quick reachability probe of the broker.
        requests.get(f"{_broker_url()}/health", timeout=5).raise_for_status()
    except Exception as e:  # noqa: BLE001 - any failure means "no live stack here"
        pytest.skip(f"cold broker stack not reachable: {e}")

    cid_a, cid_b = str(uuid.uuid4()), str(uuid.uuid4())
    rel = "year=2026/month=07/day=06/measurements.parquet"
    key_a = policy.tenant_prefix(cid_a) + rel
    body = _PARQUET_MAGIC + b"\x00integration-seed\x00" + _PARQUET_MAGIC
    try:
        s3.upload_fileobj(io.BytesIO(body), bucket, key_a)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"could not seed object store (need write creds): {e}")

    try:
        yield {"rc": rc, "s3": s3, "bucket": bucket, "cid_a": cid_a, "cid_b": cid_b,
               "rel": rel, "body": body}
    finally:
        try:
            s3.delete_object(Bucket=bucket, Key=key_a)
        except Exception:  # noqa: BLE001
            pass


def _get(handle, path, **kw):
    return requests.get(f"{_broker_url()}{path}", headers={"Authorization": f"Bearer {handle}"},
                        timeout=30, **kw)


def test_valid_handle_lists_and_reads_own_tenant(stack):
    handle = _mint(stack["rc"], company_id=stack["cid_a"])
    # List: the seeded object appears, as a path RELATIVE to the tenant prefix.
    files = _get(handle, "/cold/files").json()["files"]
    paths = {f["path"] for f in files}
    assert stack["rel"] in paths
    assert all(not p.startswith("tenant_") for p in paths)
    # Read: 307 -> pre-signed GET -> the exact bytes we seeded (Parquet magic included).
    r = _get(handle, f"/cold/object/{stack['rel']}")
    assert r.status_code == 200
    assert r.content == stack["body"]
    assert r.content[:4] == _PARQUET_MAGIC


def test_other_tenant_sees_nothing_and_cannot_read(stack):
    handle_b = _mint(stack["rc"], company_id=stack["cid_b"])
    # B's listing is empty — it never sees A's prefix.
    assert _get(handle_b, "/cold/files").json()["files"] == []
    # B asking for A's exact path is re-scoped under B's prefix -> NoSuchKey, not A's bytes.
    r = _get(handle_b, f"/cold/object/{stack['rel']}")
    assert r.content != stack["body"]
    assert b"NoSuchKey" in r.content or r.status_code >= 400


def test_wrong_audience_refused(stack):
    handle = _mint(stack["rc"], company_id=stack["cid_a"], audience="sql")
    assert _get(handle, "/cold/files").status_code == 401


def test_revoked_handle_refused(stack):
    handle = _mint(stack["rc"], company_id=stack["cid_a"])
    stack["rc"].delete(f"{_KEY_PREFIX}{handle}")  # revoke = delete the entry (ADR-0015 dec 2)
    assert _get(handle, "/cold/files").status_code == 401


def test_no_bearer_refused(stack):
    assert requests.get(f"{_broker_url()}/cold/files", timeout=30).status_code == 401
