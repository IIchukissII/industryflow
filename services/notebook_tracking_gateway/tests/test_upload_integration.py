# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
End-to-end proof for the upload plane (ADR-0030) against a live stack.

Every other upload test uses a fake object store; this one drives the REAL path: a running gateway,
real Redis (the capability store), real MinIO (the artifact store), real pre-signed PUTs. It proves
what the fakes cannot — and this file exists because the fakes demonstrably could not:

  * the gateway's IMAGE actually contains the upload plane. The unit suite imports from the source
    tree and passed while the built image crashlooped on `import admission`, because the Dockerfile's
    file list had never learned about it. This job builds and starts the real container, so that
    class of failure is a red job rather than a discovery on a box.
  * bytes really do land in staging via a pre-signed PUT, and staging really is outside every tenant
    prefix — the isolation is a claim about live object keys, not about a dict in a test.
  * the structural refusal fires on REAL bytes read back out of the store.
  * a tenant cannot commit a neighbour's upload; the tenant comes from the handle, never the request.
  * the audience holds: a kernel's tracking handle is not an uploader, in either direction.

The artifacts here are synthetic and that is exact, not a shortcut: the gateway never loads a model.
It reads each member's opening bytes and the manifest, so those bytes ARE its whole input. What it
cannot see, it cannot be tested on — loading and scoring is the serving side's gate, and its own
proof lives there.

IMPORTANT: run from INSIDE the compose network — a pre-signed URL points at the object store's
endpoint (minio:9000), which a host runner could not follow. That is itself a property: the URL is
signed for the host that will serve it.

SKIPS when the stack is unreachable; IF_REQUIRE_LIVE_STACK (conftest.py) turns that skip into a
failure in the CI job that stands the stack up.
"""
import json
import os
import sys
import uuid

import pytest

requests = pytest.importorskip("requests")
redis = pytest.importorskip("redis")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

pytestmark = pytest.mark.integration

_KEY_PREFIX = "nbcap:"

# What MLflow 3 actually writes for a non-executing artifact, and for a pickled one (probed against
# real `save_model` output). The gateway sees exactly this much: a manifest, and opening bytes.
SAFE_MANIFEST = b"""flavors:
  python_function:
    loader_module: mlflow.sklearn
    model_path: model.skops
  sklearn:
    pickled_model: model.skops
    serialization_format: skops
    sklearn_version: 1.9.0
mlflow_version: 3.14.0
"""
PICKLE_MANIFEST = SAFE_MANIFEST.replace(b"serialization_format: skops",
                                        b"serialization_format: pickle").replace(b"model.skops",
                                                                                 b"model.pkl")
ZIP_BYTES = b"PK\x03\x04\x14\x00\x00\x00" + b"\x00" * 64      # a skops file is a zip
PICKLE_BYTES = b"\x80\x05\x95\x02I\x00\x00\x00" + b"\x00" * 64  # protocol-5 framing


def _gw():
    return os.getenv("TRACKING_GATEWAY_URL", "http://notebook-tracking-gateway:5050").rstrip("/")


def _redis_client():
    return redis.Redis.from_url(os.getenv("TRACKING_REDIS_URL", "redis://redis:6379/0"))


def _mint(rc, *, company_id, audience="upload", user="e2e-uploader"):
    """Mint a handle straight into the store. The serving side's mint endpoint has its own tests;
    what is under test here is the gateway's half of the contract."""
    handle = "e2e-" + uuid.uuid4().hex
    rc.set(f"{_KEY_PREFIX}{handle}", json.dumps(
        {"user": user, "company_id": company_id, "audience": audience,
         "read_only": audience != "upload"}), ex=300)
    return handle


def _hdr(handle):
    return {"Authorization": f"Bearer {handle}"}


@pytest.fixture(scope="module")
def stack():
    try:
        rc = _redis_client()
        rc.ping()
        probe = requests.post(f"{_gw()}/api/2.0/industryflow-upload/stage", timeout=10,
                              headers=_hdr("__unminted__"), json={"files": ["MLmodel"]})
        # 401 proves the plane is served AND that an unminted handle is refused. A 404 would mean
        # the image predates the upload plane, which is exactly the failure this file is here for.
        assert probe.status_code != 404, (
            "the gateway does not serve the upload plane — the running image predates it")
        assert probe.status_code == 401, f"unexpected readiness response: {probe.status_code}"
    except AssertionError:
        raise
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"upload plane stack not reachable: {e}")
    yield {"rc": rc, "cid_a": str(uuid.uuid4()), "cid_b": str(uuid.uuid4())}


def _stage(handle, files):
    return requests.post(f"{_gw()}/api/2.0/industryflow-upload/stage", timeout=30,
                         headers=_hdr(handle), json={"files": files})


def _put(urls, blobs):
    for member, url in urls.items():
        r = requests.put(url, data=blobs[member], timeout=60)
        assert r.status_code in (200, 204), f"PUT {member} -> {r.status_code} {r.text[:120]}"


def _commit(handle, upload_id):
    return requests.post(f"{_gw()}/api/2.0/industryflow-upload/commit", timeout=60,
                         headers=_hdr(handle), json={"upload_id": upload_id})


def _upload(rc, cid, blobs):
    handle = _mint(rc, company_id=cid)
    r = _stage(handle, sorted(blobs))
    assert r.status_code == 200, f"stage -> {r.status_code} {r.text[:200]}"
    body = r.json()
    _put(body["urls"], blobs)
    return handle, body


SAFE_BLOBS = {"MLmodel": SAFE_MANIFEST, "model.skops": ZIP_BYTES}
PICKLE_BLOBS = {"MLmodel": PICKLE_MANIFEST, "model.pkl": PICKLE_BYTES}


# --- the happy path, on real bytes --------------------------------------------------------------

def test_a_non_executing_artifact_round_trips_into_the_tenants_prefix(stack):
    handle, body = _upload(stack["rc"], stack["cid_a"], SAFE_BLOBS)

    token = f"tenant_{stack['cid_a'].replace('-', '_')}"
    for url in body["urls"].values():
        assert "_upload-staging/" in url, "bytes must stage outside every tenant prefix"
        assert f"_upload-staging/{token}/" in url, "and still be partitioned per tenant"

    r = _commit(handle, body["upload_id"])
    assert r.status_code == 200, f"commit -> {r.status_code} {r.text[:200]}"
    uri = r.json()["artifact_uri"]
    assert f"{token}/uploads/{body['upload_id']}" in uri
    assert sorted(r.json()["files"]) == sorted(SAFE_BLOBS)


def test_a_committed_upload_leaves_nothing_staged(stack):
    handle, body = _upload(stack["rc"], stack["cid_a"], SAFE_BLOBS)
    assert _commit(handle, body["upload_id"]).status_code == 200
    # The handle is still valid; the staged bytes are not there any more.
    again = _commit(handle, body["upload_id"])
    assert again.status_code == 404, "an admitted artifact must not leave a second copy staged"


# --- the refusal, on real bytes -----------------------------------------------------------------

def test_an_object_serialised_artifact_is_refused_and_discarded(stack):
    handle, body = _upload(stack["rc"], stack["cid_b"], PICKLE_BLOBS)
    r = _commit(handle, body["upload_id"])
    assert r.status_code == 422, f"a pickle must be refused, got {r.status_code}"
    assert "execute author-supplied code" in str(r.json()["detail"])
    # refused bytes have no reason to persist
    assert _commit(handle, body["upload_id"]).status_code == 404


def test_an_object_stream_wearing_a_safe_name_is_refused_on_its_bytes(stack):
    """The half only a staged design can enforce: the manifest says one thing, the bytes say
    another. A mediator that merely signed a URL would never have seen this."""
    blobs = {"MLmodel": SAFE_MANIFEST, "model.skops": PICKLE_BYTES}
    handle, body = _upload(stack["rc"], stack["cid_b"], blobs)
    r = _commit(handle, body["upload_id"])
    assert r.status_code == 422
    assert "whatever the manifest says" in str(r.json()["detail"])


# --- multitenancy, through the real store -------------------------------------------------------

def test_a_tenant_cannot_commit_another_tenants_upload(stack):
    """B stages; A commits with B's id. The staging prefix is derived from the HANDLE, so the id
    alone buys nothing — and B's bytes must survive A's attempt untouched."""
    b_handle, b_body = _upload(stack["rc"], stack["cid_b"], SAFE_BLOBS)
    a_handle = _mint(stack["rc"], company_id=stack["cid_a"])

    stolen = _commit(a_handle, b_body["upload_id"])
    assert stolen.status_code == 404, f"cross-tenant commit must find nothing, got {stolen.status_code}"

    # B's upload is intact and still admits — A's attempt neither stole nor destroyed it.
    mine = _commit(b_handle, b_body["upload_id"])
    assert mine.status_code == 200
    assert f"tenant_{stack['cid_b'].replace('-', '_')}" in mine.json()["artifact_uri"]


def test_two_tenants_staging_at_once_never_share_a_path(stack):
    _, a = _upload(stack["rc"], stack["cid_a"], SAFE_BLOBS)
    _, b = _upload(stack["rc"], stack["cid_b"], SAFE_BLOBS)
    a_url = next(iter(a["urls"].values()))
    b_url = next(iter(b["urls"].values()))
    assert f"tenant_{stack['cid_a'].replace('-', '_')}" in a_url
    assert f"tenant_{stack['cid_b'].replace('-', '_')}" in b_url
    assert a["upload_id"] != b["upload_id"]


# --- the audience is a different door, live ------------------------------------------------------

def test_a_kernels_tracking_handle_is_not_an_uploader(stack):
    """Load-bearing rather than tidy: this plane refuses object-serialisation and the kernel's does
    not (ADR-0030 dec 4, deliberately). If a tracking handle opened this door, one population would
    inherit the other's rules."""
    tracking = _mint(stack["rc"], company_id=stack["cid_a"], audience="tracking")
    assert _stage(tracking, ["MLmodel"]).status_code == 401
    assert _commit(tracking, "whatever").status_code == 401


def test_an_upload_handle_does_not_open_the_kernels_tracking_plane(stack):
    upload = _mint(stack["rc"], company_id=stack["cid_a"])
    r = requests.post(f"{_gw()}/api/2.0/mlflow/experiments/create", timeout=30,
                      headers=_hdr(upload), json={"name": "should-not-work"})
    assert r.status_code == 401


@pytest.mark.parametrize("audience", ["api", "sql", "cold"])
def test_no_other_plane_reaches_the_upload_surface(stack, audience):
    handle = _mint(stack["rc"], company_id=stack["cid_a"], audience=audience)
    assert _stage(handle, ["MLmodel"]).status_code == 401


def test_a_revoked_handle_stops_working(stack):
    handle = _mint(stack["rc"], company_id=stack["cid_a"])
    assert _stage(handle, ["MLmodel"]).status_code == 200
    stack["rc"].delete(f"{_KEY_PREFIX}{handle}")     # revocation IS deletion (ADR-0015 dec 2)
    assert _stage(handle, ["MLmodel"]).status_code == 401


def test_no_bearer_is_refused(stack):
    r = requests.post(f"{_gw()}/api/2.0/industryflow-upload/stage", timeout=30,
                      json={"files": ["MLmodel"]})
    assert r.status_code == 401


# --- the gateway decides the keys, not the caller ------------------------------------------------

def test_unusable_member_paths_are_refused_by_the_live_gateway(stack):
    handle = _mint(stack["rc"], company_id=stack["cid_a"])
    for bad in ("../escape", "/abs", "a/../b"):
        r = _stage(handle, ["MLmodel", bad])
        assert r.status_code == 400, f"{bad} -> {r.status_code}"


def test_an_artifact_without_a_manifest_is_refused_before_a_url_is_signed(stack):
    handle = _mint(stack["rc"], company_id=stack["cid_a"])
    assert _stage(handle, ["model.skops"]).status_code == 400
