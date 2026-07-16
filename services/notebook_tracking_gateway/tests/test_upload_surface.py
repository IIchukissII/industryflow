# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The upload plane's wire surface (ADR-0030): stage → judge → admit or discard.

Driven with a fake store, like the rest of the gateway's tests. The properties that matter are the
ones a fake can still prove honestly:

  * **nothing unadmitted reaches the tenant's prefix** — the whole reason the design is staged;
  * **the tenant is the handle's**, never the request's, so an upload cannot be staged into or
    committed out of a neighbour's space;
  * **a refused artifact leaves nothing behind**;
  * **the audience separates the populations** — a kernel's tracking handle is not an uploader.
"""
import json
import os
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gateway  # noqa: E402
import policy  # noqa: E402
import staging  # noqa: E402

CID_A = str(uuid.uuid4())
CID_B = str(uuid.uuid4())
TOKEN_A = policy.tenant_token(CID_A)
TOKEN_B = policy.tenant_token(CID_B)

SAFE_MANIFEST = b"""flavors:
  sklearn:
    pickled_model: model.skops
    serialization_format: skops
mlflow_version: 3.14.0
"""
PICKLE_MANIFEST = SAFE_MANIFEST.replace(b"serialization_format: skops", b"serialization_format: pickle")
ZIP_HEAD = b"PK\x03\x04\x14\x00\x00\x00"
PICKLE_HEAD = b"\x80\x05\x95\x02I\x00\x00\x00"


class FakeStore:
    """An object store: opaque keys to bytes. Records copies/deletes so the tests can ask what the
    gateway actually did, not what it returned."""

    def __init__(self):
        self.objects = {}
        self.presigned = []

    def presign(self, method, key):
        self.presigned.append((method, key))
        return f"https://store.invalid/{key}?sig=x"

    def list_files(self, prefix):
        return []

    def delete(self, key):
        self.objects.pop(key, None)

    def head(self, key, length):
        v = self.objects.get(key)
        return v[:length] if v is not None else None

    def copy(self, src, dst):
        self.objects[dst] = self.objects[src]

    def list_keys(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))


def _client(store, **handles):
    async def store_get(key):
        return handles.get(key)

    class Upstream:
        async def call(self, method, endpoint, *, params, body):
            return 200, {}

    return TestClient(gateway.build_app(store_get, Upstream(), store, bucket="mlflow"))


def _handle(audience, company_id):
    return json.dumps({"user": "alice", "company_id": company_id, "audience": audience,
                       "read_only": audience != "upload"})


def _upload_client(store, company_id=CID_A):
    return _client(store, **{"nbcap:up": _handle("upload", company_id)})


def _stage(client, files=("MLmodel", "model.skops")):
    r = client.post("/api/2.0/industryflow-upload/stage",
                    json={"files": list(files)},
                    headers={"Authorization": "Bearer up"})
    return r


# --- auth: the audience is what makes this a different door -----------------------------------

def test_a_kernels_tracking_handle_is_not_an_uploader():
    store = FakeStore()
    c = _client(store, **{"nbcap:tr": _handle("tracking", CID_A)})
    r = c.post("/api/2.0/industryflow-upload/stage", json={"files": ["MLmodel"]},
               headers={"Authorization": "Bearer tr"})
    assert r.status_code == 401


def test_no_handle_is_refused():
    store = FakeStore()
    c = _upload_client(store)
    assert _stage(_client(store)).status_code == 401
    assert c.post("/api/2.0/industryflow-upload/stage", json={"files": ["MLmodel"]}).status_code == 401


# --- stage: the gateway decides every key ------------------------------------------------------

def test_stage_returns_one_presigned_put_per_member_under_staging():
    store = FakeStore()
    r = _stage(_upload_client(store))
    assert r.status_code == 200
    body = r.json()
    assert set(body["urls"]) == {"MLmodel", "model.skops"}
    for method, key in store.presigned:
        assert method == "PUT"
        assert key.startswith(staging.STAGING_ROOT + TOKEN_A + "/")


def test_staged_keys_are_outside_every_tenant_prefix():
    """The artifact plane rewrites any key under `tenant_<uuid>/`; staging is rooted elsewhere, so a
    tenant cannot reach its own unadmitted bytes through the ordinary artifact plane."""
    store = FakeStore()
    _stage(_upload_client(store))
    for _, key in store.presigned:
        assert not key.startswith(policy.artifact_prefix(CID_A))
        assert not policy.owns_artifact_key(CID_A, key)


def test_stage_refuses_an_artifact_with_no_manifest():
    r = _stage(_upload_client(FakeStore()), files=["model.skops"])
    assert r.status_code == 400


def test_stage_refuses_unusable_member_paths():
    store = FakeStore()
    for bad in ("../escape", "/abs", "a/../b", "", "x\\y"):
        r = _stage(_upload_client(store), files=["MLmodel", bad])
        assert r.status_code == 400, bad
    assert store.presigned == []      # nothing was signed for any of them


def test_stage_refuses_an_unbounded_member_list():
    files = ["MLmodel"] + [f"f{i}" for i in range(staging.MAX_MEMBERS + 5)]
    assert _stage(_upload_client(FakeStore()), files=files).status_code == 400


def test_two_uploads_never_share_a_path():
    store = FakeStore()
    c = _upload_client(store)
    assert _stage(c).json()["upload_id"] != _stage(c).json()["upload_id"]


# --- commit: admitted ---------------------------------------------------------------------------

def _staged(store, token, upload_id, manifest=SAFE_MANIFEST, model_head=ZIP_HEAD):
    p = staging.staging_prefix(token, upload_id)
    store.objects[p + "MLmodel"] = manifest
    store.objects[p + "model.skops"] = model_head


def test_commit_admits_a_safe_artifact_into_the_tenants_own_prefix():
    store = FakeStore()
    c = _upload_client(store)
    uid = _stage(c).json()["upload_id"]
    _staged(store, TOKEN_A, uid)

    r = c.post("/api/2.0/industryflow-upload/commit", json={"upload_id": uid},
               headers={"Authorization": "Bearer up"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["artifact_uri"] == f"s3://mlflow/{policy.artifact_prefix(CID_A)}uploads/{uid}"
    admitted = [k for k in store.objects if k.startswith(policy.artifact_prefix(CID_A))]
    assert sorted(admitted) == sorted(
        f"{policy.artifact_prefix(CID_A)}uploads/{uid}/{m}" for m in ("MLmodel", "model.skops"))
    # and the staging area is emptied — an admitted artifact does not leave a second copy behind
    assert store.list_keys(staging.staging_prefix(TOKEN_A, uid)) == []


def test_commit_refuses_nothing_staged():
    c = _upload_client(FakeStore())
    r = c.post("/api/2.0/industryflow-upload/commit", json={"upload_id": staging.new_upload_id()},
               headers={"Authorization": "Bearer up"})
    assert r.status_code == 404


def test_commit_refuses_an_unusable_upload_id():
    c = _upload_client(FakeStore())
    for bad in ("../../etc", "", "x", None, 5):
        r = c.post("/api/2.0/industryflow-upload/commit", json={"upload_id": bad},
                   headers={"Authorization": "Bearer up"})
        assert r.status_code == 400, bad


# --- commit: refused, and nothing survives ------------------------------------------------------

def test_a_declared_object_stream_is_refused_and_discarded():
    store = FakeStore()
    c = _upload_client(store)
    uid = _stage(c).json()["upload_id"]
    _staged(store, TOKEN_A, uid, manifest=PICKLE_MANIFEST, model_head=PICKLE_HEAD)

    r = c.post("/api/2.0/industryflow-upload/commit", json={"upload_id": uid},
               headers={"Authorization": "Bearer up"})
    assert r.status_code == 422
    assert "execute author-supplied code" in r.json()["detail"]
    # nothing admitted, nothing left staged
    assert [k for k in store.objects if k.startswith(policy.artifact_prefix(CID_A))] == []
    assert store.list_keys(staging.staging_prefix(TOKEN_A, uid)) == []


def test_an_object_stream_wearing_a_safe_name_is_refused_on_its_bytes():
    """The half that only a staged design can enforce: the manifest says skops, the bytes say
    otherwise. A mediator that merely signed a URL would never have seen this."""
    store = FakeStore()
    c = _upload_client(store)
    uid = _stage(c).json()["upload_id"]
    _staged(store, TOKEN_A, uid, manifest=SAFE_MANIFEST, model_head=PICKLE_HEAD)

    r = c.post("/api/2.0/industryflow-upload/commit", json={"upload_id": uid},
               headers={"Authorization": "Bearer up"})
    assert r.status_code == 422
    assert "whatever the manifest says" in r.json()["detail"]
    assert [k for k in store.objects if k.startswith(policy.artifact_prefix(CID_A))] == []


# --- multitenancy: the tenant is the handle's, never the request's ------------------------------

def test_a_tenant_cannot_commit_another_tenants_staged_upload():
    """B stages; A commits with B's id. A's commit must not reach B's staging area — the prefix is
    derived from the handle, so the id alone buys nothing."""
    store = FakeStore()
    b = _upload_client(store, company_id=CID_B)
    uid = _stage(b).json()["upload_id"]
    _staged(store, TOKEN_B, uid)

    a = _client(store, **{"nbcap:up": _handle("upload", CID_A)})
    r = a.post("/api/2.0/industryflow-upload/commit", json={"upload_id": uid},
               headers={"Authorization": "Bearer up"})
    assert r.status_code == 404                      # A's staging prefix holds nothing
    # B's bytes are untouched, and nothing landed in A's prefix
    assert store.list_keys(staging.staging_prefix(TOKEN_B, uid)) != []
    assert [k for k in store.objects if k.startswith(policy.artifact_prefix(CID_A))] == []


def test_staging_is_partitioned_by_tenant():
    store = FakeStore()
    a_uid = _stage(_upload_client(store, company_id=CID_A)).json()["upload_id"]
    b_uid = _stage(_client(store, **{"nbcap:up": _handle("upload", CID_B)})).json()["upload_id"]
    a_keys = [k for _, k in store.presigned if TOKEN_A in k]
    b_keys = [k for _, k in store.presigned if TOKEN_B in k]
    assert a_keys and b_keys
    assert all(TOKEN_B not in k for k in a_keys)
    assert all(TOKEN_A not in k for k in b_keys)


def test_admitted_bytes_land_only_in_the_handles_own_tenant_prefix():
    store = FakeStore()
    c = _upload_client(store, company_id=CID_B)
    uid = _stage(c).json()["upload_id"]
    _staged(store, TOKEN_B, uid)
    c.post("/api/2.0/industryflow-upload/commit", json={"upload_id": uid},
           headers={"Authorization": "Bearer up"})
    assert [k for k in store.objects if k.startswith(policy.artifact_prefix(CID_A))] == []
    assert [k for k in store.objects if k.startswith(policy.artifact_prefix(CID_B))] != []
