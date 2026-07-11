# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Broker orchestration tests (ADR-0025 dec 5, read side): bearer auth, tenant-prefix scoping,
read-only presigned-GET redirects, cross-tenant refusal — driven through a fake store + object
store, no Redis and no S3.
"""
import json
import uuid

from fastapi.testclient import TestClient

import broker  # noqa: E402

CID = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
PREFIX = f"tenant_{CID}/"


class FakeStore:
    def __init__(self, mapping):
        self._m = mapping

    async def get(self, key):
        return self._m.get(key)


class FakeColdStore:
    def __init__(self):
        self.presigned = []
        # keys the tenant "owns" in the object store, relative paths returned by list
        self.objects = {
            PREFIX + "year=2026/month=07/day=06/measurements.parquet": 4961,
            PREFIX + "year=2026/month=07/day=06/_manifest.json": 80,
        }

    def presign_get(self, key):
        self.presigned.append(key)
        return f"https://obj.store/{key}?sig=abc"

    def list_files(self, prefix):
        p = prefix.rstrip("/") + "/"
        return [{"path": k[len(p):], "size": v} for k, v in self.objects.items() if k.startswith(p)]


def _handle(mapping, audience="cold", cid=CID):
    mapping["nbcap:tok"] = json.dumps({"user": "u", "company_id": cid, "audience": audience})
    return "tok"


def _client(store_map, cold):
    return TestClient(broker.build_app(FakeStore(store_map).get, cold))


def _auth(tok):
    return {"authorization": f"Bearer {tok}"}


def test_health_open():
    assert _client({}, FakeColdStore()).get("/health").json() == {"status": "ok"}


def test_missing_bearer_denied():
    r = _client({}, FakeColdStore()).get("/cold/files")
    assert r.status_code == 401


def test_wrong_audience_denied():
    m = {}
    tok = _handle(m, audience="sql")
    r = _client(m, FakeColdStore()).get("/cold/files", headers=_auth(tok))
    assert r.status_code == 401


def test_list_files_scoped_to_tenant():
    m = {}; tok = _handle(m); cold = FakeColdStore()
    r = _client(m, cold).get("/cold/files", headers=_auth(tok))
    assert r.status_code == 200
    files = {f["path"] for f in r.json()["files"]}
    # paths are RELATIVE to the tenant prefix (the kernel never sees tenant_<uuid>/)
    assert "year=2026/month=07/day=06/measurements.parquet" in files
    assert all(not p.startswith("tenant_") for p in files)


def test_get_object_redirects_to_presigned_scoped_key():
    m = {}; tok = _handle(m); cold = FakeColdStore()
    r = _client(m, cold).get("/cold/object/year=2026/month=07/day=06/measurements.parquet",
                             headers=_auth(tok), follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith(f"https://obj.store/{PREFIX}")
    # the broker presigned a key under the caller's prefix only
    assert cold.presigned == [PREFIX + "year=2026/month=07/day=06/measurements.parquet"]


def test_cross_tenant_path_lands_under_caller_prefix():
    # A crafted absolute other-tenant path is treated as relative and re-prefixed under the caller.
    other = str(uuid.uuid4())
    m = {}; tok = _handle(m); cold = FakeColdStore()
    r = _client(m, cold).get(f"/cold/object/tenant_{other}/steal.parquet",
                             headers=_auth(tok), follow_redirects=False)
    assert r.status_code == 307
    assert cold.presigned[0].startswith(PREFIX)
    assert f"tenant_{other}/steal.parquet" in cold.presigned[0]  # nested under caller, not the other tenant


def test_dotdot_path_rejected():
    # A URL-path '..' is normalised away by the HTTP layer before routing (so it can't traverse);
    # the query-param surface is where '..' survives literally, and scope_key rejects it -> 400.
    m = {}; tok = _handle(m)
    r = _client(m, FakeColdStore()).get("/cold/files", params={"path": "../tenant_other"},
                                        headers=_auth(tok))
    assert r.status_code == 400


def test_read_only_no_write_verbs():
    m = {}; tok = _handle(m)
    c = _client(m, FakeColdStore())
    assert c.put("/cold/object/x.parquet", headers=_auth(tok)).status_code == 405
    assert c.delete("/cold/object/x.parquet", headers=_auth(tok)).status_code == 405
