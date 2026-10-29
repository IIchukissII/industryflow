# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tracking gateway's part of the capability-use record (ADR-0042).

Both audiences it resolves are recorded: a handle's first use by this plane once (dec 5), every
refusal by the presented value's digest and never the value (dec 2), and an upload handle's
consumption in the same atomic step as the claim that consumes it (dec 3). The operator plane is a
shared secret, not a capability, and records nothing.

Most of this runs on the dict fake (capability_audit_fake.py). The race runs on fakeredis, so the
contract's real Lua decides which of two commits holding one handle wins.
"""
import asyncio
import hashlib
import json
import os
import sys
import uuid

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient
from industryflow_contracts import capability_audit as audit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gateway  # noqa: E402
import policy  # noqa: E402
import staging  # noqa: E402
from capability_audit_fake import Capabilities  # noqa: E402

CID = str(uuid.uuid4())
TOKEN = policy.tenant_token(CID)
HANDLE_ID = str(uuid.uuid4())
# Bearer secrets, distinct enough that finding one in the record cannot be a coincidence.
TR = "tr-" + uuid.uuid4().hex
UP = "up-" + uuid.uuid4().hex
SAFE_MANIFEST = b"""flavors:
  sklearn:
    pickled_model: model.skops
    serialization_format: skops
mlflow_version: 3.14.0
"""
ZIP_HEAD = b"PK\x03\x04\x14\x00\x00\x00"
UPLOAD = "/api/2.0/industryflow-upload"


class Upstream:
    async def call(self, method, endpoint, *, params, body):
        if endpoint == "experiments/get-by-name":
            return 200, {"experiment": {"experiment_id": "1", "name": params.get("experiment_name")}}
        return 200, {}


class Artifacts:
    def __init__(self):
        self.objects = {}

    def presign(self, method, key):
        return f"https://store.invalid/{key}"

    def list_files(self, prefix):
        return []

    def delete(self, key):
        self.objects.pop(key, None)

    def head(self, key, length):
        v = self.objects.get(key)
        return None if v is None else (v[:length], '"' + hashlib.md5(v).hexdigest() + '"')

    def copy(self, src, dst, if_match):
        self.objects[dst] = self.objects[src]
        return True

    def list_keys(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))


def _entry(audience, *, handle_id=HANDLE_ID, company_id=CID):
    record = {"user": "alice", "company_id": company_id, "audience": audience,
              "read_only": audience != "upload"}
    if handle_id is not None:
        record["handle_id"] = handle_id
    return json.dumps(record)


def _client(caps, art=None, **kw):
    return TestClient(gateway.build_app(caps, Upstream(), art or Artifacts(), bucket="mlflow", **kw))


def _bearer(handle):
    return {"Authorization": f"Bearer {handle}"}


def _track(client, handle):
    return client.get("/api/2.0/mlflow/experiments/get-by-name", params={"experiment_name": "e"},
                      headers=_bearer(handle))


def _staged(art, upload_id):
    p = staging.staging_prefix(TOKEN, upload_id)
    art.objects[p + "MLmodel"] = SAFE_MANIFEST
    art.objects[p + "model.skops"] = ZIP_HEAD


def _no_handle_in(stream, handle):
    for e in stream:
        assert all(handle not in str(v) for v in e.values()), "a bearer handle entered the record"


# --- the tracking audience -------------------------------------------------------------------------

def test_a_tracking_handles_first_use_is_recorded_once():
    caps = Capabilities({f"nbcap:{TR}": _entry("tracking")})
    c = _client(caps)
    for _ in range(3):
        assert _track(c, TR).status_code == 200

    [first] = caps.events()
    assert first["kind"] == audit.FIRST_USE and first["plane"] == "tracking_gateway"
    assert (first["handle_id"], first["user"], first["company_id"], first["audience"]) == \
        (HANDLE_ID, "alice", CID, "tracking")
    _no_handle_in(caps.stream, TR)


def test_an_entry_minted_before_the_record_is_served_and_has_no_first_use():
    caps = Capabilities({f"nbcap:{TR}": _entry("tracking", handle_id=None)})
    assert _track(_client(caps), TR).status_code == 200
    assert caps.events() == []


def test_an_unknown_handle_is_refused_by_its_digest_never_the_handle():
    caps = Capabilities()
    c = _client(caps)
    secret = "kernel-secret-" + uuid.uuid4().hex
    assert _track(c, secret).status_code == 401
    assert _track(c, secret).status_code == 401

    refusals = caps.events(audit.REFUSED)
    assert len(refusals) == 2, "every refusal is recorded, not only the first"
    for r in refusals:
        assert r["reason"] == "no_entry" and r["plane"] == "tracking_gateway"
        assert r["digest"] == audit.presented_digest(secret)
        assert r["handle_id"] is None
    _no_handle_in(caps.stream, secret)


def test_a_wrong_audience_refusal_names_what_the_entry_was():
    caps = Capabilities({f"nbcap:{UP}": _entry("upload")})
    assert _track(_client(caps), UP).status_code == 401
    [r] = caps.events()
    assert r["kind"] == audit.REFUSED and r["reason"] == "wrong_audience"
    assert (r["handle_id"], r["audience"], r["company_id"]) == (HANDLE_ID, "upload", CID)


def test_a_suspended_tenants_handle_is_refused_and_recorded():
    caps = Capabilities({f"nbcap:{TR}": _entry("tracking"), f"nbcap-suspended:{CID}": "1"})
    assert _track(_client(caps), TR).status_code == 401
    [r] = caps.events()
    assert r["reason"] == "tenant_suspended" and r["handle_id"] == HANDLE_ID


def test_a_request_presenting_no_handle_records_nothing():
    caps = Capabilities()
    assert _client(caps).get("/api/2.0/mlflow/experiments/get-by-name").status_code == 401
    assert caps.events() == []


def test_the_operator_plane_is_not_a_capability_and_records_nothing():
    caps = Capabilities()
    c = _client(caps, operator_token="op-secret")
    assert c.delete(f"/api/2.0/industryflow-operator/tenants/{CID}",
                    headers=_bearer("wrong")).status_code == 401
    assert caps.events() == []


# --- the upload audience ---------------------------------------------------------------------------

def test_an_upload_is_recorded_as_first_use_then_consumption():
    caps, art = Capabilities({f"nbcap:{UP}": _entry("upload")}), Artifacts()
    c = _client(caps, art)
    uid = c.post(UPLOAD + "/stage", json={"files": ["MLmodel", "model.skops"]},
                 headers=_bearer(UP)).json()["upload_id"]
    _staged(art, uid)
    assert c.post(UPLOAD + "/commit", json={"upload_id": uid}, headers=_bearer(UP)).status_code == 200

    first, consumed = caps.events()
    assert first["kind"] == audit.FIRST_USE and first["audience"] == "upload"
    assert consumed["kind"] == audit.CONSUMED and consumed["plane"] == "tracking_gateway"
    assert (consumed["handle_id"], consumed["user"], consumed["company_id"], consumed["audience"]) == \
        (HANDLE_ID, "alice", CID, "upload")

    # Spent: presenting it again is a refusal, and consumes nothing more.
    assert c.post(UPLOAD + "/commit", json={"upload_id": uid}, headers=_bearer(UP)).status_code == 401
    assert len(caps.events(audit.CONSUMED)) == 1
    assert caps.events()[-1]["reason"] == "no_entry"
    _no_handle_in(caps.stream, UP)


def test_a_tracking_handle_at_the_upload_surface_is_refused_and_recorded():
    caps = Capabilities({f"nbcap:{TR}": _entry("tracking")}, consumable=False)
    r = _client(caps).post(UPLOAD + "/stage", json={"files": ["MLmodel"]}, headers=_bearer(TR))
    assert r.status_code == 401
    [e] = caps.events()
    assert e["kind"] == audit.REFUSED and e["reason"] == "wrong_audience" and e["audience"] == "tracking"


class _ClaimRace:
    """fakeredis, holding every claim until two callers have reached it: both commits have resolved
    the handle before either consumes it, so only the claim can decide between them."""

    def __init__(self, redis):
        self.redis = redis
        self.barrier = asyncio.Barrier(2)

    async def get(self, key):
        return await self.redis.get(key)

    async def xadd(self, *a, **kw):
        return await self.redis.xadd(*a, **kw)

    async def eval(self, script, numkeys, *keys_and_args):
        if script == audit.DELETE_RECORDED_SCRIPT:
            await asyncio.wait_for(self.barrier.wait(), timeout=5)
        return await self.redis.eval(script, numkeys, *keys_and_args)


@pytest.mark.asyncio
async def test_two_commits_on_one_handle_one_consumes_and_one_consumption_is_recorded():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    await redis.set(f"nbcap:{UP}", _entry("upload"))
    art = Artifacts()
    uid = staging.new_upload_id()
    _staged(art, uid)
    app = gateway.build_app(_ClaimRace(redis), Upstream(), art, bucket="mlflow")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as c:
        a, b = await asyncio.gather(*(c.post(UPLOAD + "/commit", json={"upload_id": uid},
                                             headers=_bearer(UP)) for _ in range(2)))

    assert sorted([a.status_code, b.status_code]) == [200, 401], "exactly one caller wins the claim"
    assert await redis.get(f"nbcap:{UP}") is None
    events = [audit.decode(f) for _id, f in await redis.xrange(audit.STREAM_KEY)]
    kinds = [e["kind"] for e in events]
    assert kinds.count(audit.CONSUMED) == 1
    assert kinds.count(audit.FIRST_USE) == 1
    [lost] = [e for e in events if e["kind"] == audit.REFUSED]
    assert lost["reason"] == "already_consumed" and lost["handle_id"] == HANDLE_ID
    for _id, f in await redis.xrange(audit.STREAM_KEY):
        assert all(UP not in v for v in f.values()), "a bearer handle entered the record"
