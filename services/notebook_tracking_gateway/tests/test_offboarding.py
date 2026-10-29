# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The operator plane that empties a deleted tenant out of the tracking store (ADR-0033 dec 4), against a
fake MLflow that keeps state: what must be gone is gone, what belongs to anyone else is untouched,
and only the job's secret reaches it.
"""
import os
import re
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gateway  # noqa: E402
from capability_audit_fake import Capabilities  # noqa: E402
from industryflow_contracts import tenancy  # noqa: E402

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
SECRET = "operator-secret-0123456789"
MINE, THEIRS = tenancy.mlflow_name_prefix(CID), tenancy.mlflow_name_prefix(OTHER)
GONE = tenancy.mlflow_deleted_name_prefix(CID)


class FakeMlflow:
    """Just enough of MLflow's REST semantics: lifecycle, rename, LIKE filters, paging."""

    def __init__(self, page=2):
        self.page = page
        self.experiments = {}      # id -> {"name", "lifecycle_stage"}
        self.logged_models = {}    # id -> experiment id (active ones only; a deleted one is invisible)
        self.registered = set()
        self.fail = None

    def add_experiment(self, name, stage="active"):
        eid = str(len(self.experiments) + 1)
        self.experiments[eid] = {"name": name, "lifecycle_stage": stage}
        return eid

    def _page(self, items, token):
        start = int(token or 0)
        rest = items[start:start + self.page]
        nxt = str(start + self.page) if start + self.page < len(items) else None
        return rest, nxt

    @staticmethod
    def _like(pattern, name):
        rx = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
        return re.fullmatch(rx, name) is not None

    async def call(self, method, endpoint, *, params, body):
        msg = params if method == "GET" else body
        if self.fail == endpoint:
            return 500, {"error_code": "INTERNAL_ERROR"}
        if endpoint == "experiments/search":
            pattern = re.fullmatch(r"name LIKE '(.*)'", msg["filter"]).group(1)
            hits = [dict(v, experiment_id=k) for k, v in sorted(self.experiments.items())
                    if self._like(pattern, v["name"])]
            items, nxt = self._page(hits, msg.get("page_token"))
            return 200, {"experiments": items, **({"next_page_token": nxt} if nxt else {})}
        if endpoint == "experiments/restore":
            self.experiments[msg["experiment_id"]]["lifecycle_stage"] = "active"
            return 200, {}
        if endpoint == "experiments/update":
            exp = self.experiments[msg["experiment_id"]]
            if exp["lifecycle_stage"] != "active":
                return 400, {"error_code": "INVALID_STATE"}
            exp["name"] = msg["new_name"]
            return 200, {}
        if endpoint == "experiments/delete":
            self.experiments[msg["experiment_id"]]["lifecycle_stage"] = "deleted"
            return 200, {}
        if endpoint == "logged-models/search":
            hits = [{"info": {"model_id": m}} for m, e in sorted(self.logged_models.items()) if e in msg["experiment_ids"]]
            items, nxt = self._page(hits, msg.get("page_token"))
            return 200, {"models": items, **({"next_page_token": nxt} if nxt else {})}
        if method == "DELETE" and endpoint.startswith("logged-models/"):
            return (200, {}) if self.logged_models.pop(endpoint.split("/", 1)[1], None) else (404, {})
        if endpoint == "registered-models/search":
            pattern = re.fullmatch(r"name LIKE '(.*)'", msg["filter"]).group(1)
            hits = [{"name": n} for n in sorted(self.registered) if self._like(pattern, n)]
            items, nxt = self._page(hits, msg.get("page_token"))
            return 200, {"registered_models": items, **({"next_page_token": nxt} if nxt else {})}
        if endpoint == "registered-models/delete":
            if msg["name"] not in self.registered:
                return 404, {"error_code": "RESOURCE_DOES_NOT_EXIST"}
            self.registered.discard(msg["name"])
            return 200, {}
        raise AssertionError(f"unexpected call {method} {endpoint}")

    def collect(self):
        """The tracking server's collection: hard-deletes the deleted experiments under the root."""
        for eid in [k for k, v in self.experiments.items()
                    if v["lifecycle_stage"] == "deleted" and v["name"].startswith(tenancy.MLFLOW_DELETED_NAME_ROOT)]:
            del self.experiments[eid]


class FakeArtifacts:
    def __init__(self, keys):
        self.keys = set(keys)

    def list_keys(self, prefix):
        return sorted(k for k in self.keys if k.startswith(prefix))

    def delete(self, key):
        self.keys.discard(key)


def _seed():
    ml = FakeMlflow()
    live = ml.add_experiment(MINE + "exp")
    trashed = ml.add_experiment(MINE + "trashed", "deleted")
    ml.add_experiment(MINE + "third")
    theirs = ml.add_experiment(THEIRS + "exp")
    their_trash = ml.add_experiment(THEIRS + "trash", "deleted")
    # LIKE reads `_` as any character: this name matches the tenant's pattern and is not the tenant's.
    lookalike = ml.add_experiment(MINE.replace("_", "X", 1) + "exp")
    ml.logged_models = {"m1": live, "m2": live, "m3": trashed, "mo": theirs}
    ml.registered = {MINE + "model", MINE + "model2", MINE + "model3", THEIRS + "model"}
    art = FakeArtifacts({
        tenancy.mlflow_artifact_prefix(CID) + "abc/artifacts/model.skops",
        tenancy.mlflow_artifact_prefix(CID) + "uploads/u1/model.skops",
        f"_upload-staging/{tenancy.tenant_token(CID)}/u2/m",
        tenancy.mlflow_artifact_prefix(OTHER) + "abc/artifacts/model.skops",
        f"_upload-staging/{tenancy.tenant_token(OTHER)}/u3/m",
    })
    return ml, art, {"theirs": theirs, "their_trash": their_trash, "lookalike": lookalike}


def _client(ml, art, token=SECRET):
    return TestClient(gateway.build_app(Capabilities(consumable=False), ml, art, operator_token=token))


def _empty(client, cid=CID, secret=SECRET):
    return client.delete(f"/api/2.0/industryflow-operator/tenants/{cid}", headers={"Authorization": f"Bearer {secret}"})


def test_the_tenant_is_emptied_and_nobody_else_is_touched():
    ml, art, others = _seed()
    before_theirs = {k: dict(v) for k, v in ml.experiments.items() if k in others.values()}

    r = _empty(_client(ml, art))

    assert r.status_code == 200, r.text
    mine = {k: v for k, v in ml.experiments.items() if k not in others.values()}
    assert len(mine) == 3
    assert all(v["name"] == GONE + k and v["lifecycle_stage"] == "deleted" for k, v in mine.items()), mine
    assert {k: v for k, v in ml.experiments.items() if k in others.values()} == before_theirs
    assert set(ml.logged_models) == {"mo"}
    assert ml.registered == {THEIRS + "model"}
    assert art.keys == {tenancy.mlflow_artifact_prefix(OTHER) + "abc/artifacts/model.skops",
                        f"_upload-staging/{tenancy.tenant_token(OTHER)}/u3/m"}
    # The deleted experiments remain until the tracking server collects them.
    assert r.json() == {"company_id": CID, "remaining": {"experiments": 3, "registered_models": 0, "artifacts": 0}}


def test_once_collected_nothing_remains_and_a_repeat_changes_nothing():
    ml, art, _ = _seed()
    client = _client(ml, art)
    assert _empty(client).status_code == 200
    ml.collect()

    r = _empty(client)

    assert r.status_code == 200
    assert r.json()["remaining"] == {"experiments": 0, "registered_models": 0, "artifacts": 0}
    # Another tenant's own trash is not the collection's to take.
    assert any(v["name"] == THEIRS + "trash" for v in ml.experiments.values())


def test_a_call_that_died_part_way_is_finished_by_the_next():
    ml, art, _ = _seed()
    client = _client(ml, art)
    ml.fail = "experiments/delete"
    assert _empty(client).status_code == 502
    renamed_but_live = [v for v in ml.experiments.values() if v["name"].startswith(GONE) and v["lifecycle_stage"] == "active"]
    assert renamed_but_live

    ml.fail = None
    assert _empty(client).status_code == 200

    assert all(v["lifecycle_stage"] == "deleted" for v in ml.experiments.values() if v["name"].startswith(GONE))
    assert not any(v["name"].startswith(MINE) for v in ml.experiments.values())


@pytest.mark.parametrize("secret", ["", "wrong", "track-handle"])
def test_only_the_operator_secret_reaches_it(secret):
    ml, art, _ = _seed()
    r = _empty(_client(ml, art), secret=secret)
    assert r.status_code == 401
    assert len(ml.registered) == 4


def test_without_a_configured_secret_the_surface_does_not_exist():
    ml, art, _ = _seed()
    r = _empty(_client(ml, art, token=None), secret="")
    assert r.status_code in (404, 405)
    assert len(ml.registered) == 4


def test_a_tenant_id_must_be_a_tenant_id():
    ml, art, _ = _seed()
    assert _empty(_client(ml, art), cid="tenant_x%").status_code == 400
