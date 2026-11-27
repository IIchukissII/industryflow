# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant over its object-store cap is refused its artifact writes, naming the cap, and the refusal is
counted (ADR-0049 dec 5, 12); its reads and deletes go on, and nothing is deleted for it (dec 13)."""
import json
import os
import sys
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gateway  # noqa: E402
import policy  # noqa: E402
from capability_store import capabilities  # noqa: E402
from industryflow_contracts import quotas  # noqa: E402

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


class Upstream:
    async def call(self, method, endpoint, *, params, body):
        return 200, {}


class Artifacts:
    def __init__(self):
        self.deleted, self.signed = [], []

    def presign(self, method, key):
        self.signed.append((method, key))
        return f"https://store.local/{key}"

    def list_files(self, prefix):
        return []

    def delete(self, key):
        self.deleted.append(key)


def _client(over=(CID,)):
    entries = {f"{policy._KEY_PREFIX}tr-{cid}": json.dumps(
        {"user": "ds", "company_id": cid, "audience": "tracking", "read_only": False}) for cid in (CID, OTHER)}
    entries[f"{policy._KEY_PREFIX}up"] = json.dumps(
        {"user": "ds", "company_id": CID, "audience": "upload", "read_only": False})
    entries.update({quotas.over_key(quotas.STORED_OBJECT_GIB, cid): "1" for cid in over})
    store = capabilities(entries)
    art = Artifacts()
    client = TestClient(gateway.build_app(store, Upstream(), art, bucket="mlflow", quota_store=store))
    client.store, client.art = store, art
    return client


def _put(client, cid):
    return client.put("/api/2.0/mlflow-artifacts/artifacts/5/run1/artifacts/model.pkl", content=b"x",
                      headers={"Authorization": f"Bearer tr-{cid}"}, follow_redirects=False)


def test_an_artifact_write_is_refused_naming_the_cap_and_counted():
    c = _client()
    r = _put(c, CID)
    assert r.status_code == 507 and "stored_object_gib" in r.json()["detail"]
    assert int(r.headers["retry-after"]) > 1
    assert c.art.signed == []                          # no URL to write with was handed out
    assert _put(c, OTHER).status_code == 307           # another tenant writes on
    with c.store.sync() as store:
        assert int(store.get(quotas.refusals_total_key(quotas.STORED_OBJECT_GIB))) == 1


def test_reads_and_deletes_go_on_and_nothing_is_deleted_for_the_cap():
    c = _client()
    auth = {"Authorization": f"Bearer tr-{CID}"}
    path = "/api/2.0/mlflow-artifacts/artifacts/5/run1/artifacts/model.pkl"
    assert c.get(path, headers=auth, follow_redirects=False).status_code == 307
    assert c.art.deleted == []
    assert c.delete(path, headers=auth).status_code == 200


def test_an_upload_is_refused_at_its_staging():
    c = _client()
    r = c.post("/api/2.0/industryflow-upload/stage", json={"files": ["MLmodel"]},
               headers={"Authorization": "Bearer up"})
    assert r.status_code == 507 and c.art.signed == []


def test_a_tenant_under_its_cap_writes():
    assert _put(_client(over=()), CID).status_code == 307
