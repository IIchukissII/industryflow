# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every route that changes tenant data needs a writer role (engineer or admin) or a superuser; an
observer is read-only and gets 403 (docs/operations/user-management.md, ADR-0004 dec 5).

The role is the account's, re-read on each request through the real ``verify_jwt_token`` against a
fake account store. Downstream (repository, capability store, MLflow client, registration gate) is a
recorder that stops the request, so "allowed" means the handler body was reached and "refused" means
it was not.

The guard at the bottom enumerates every non-GET route on the real app: each one is gated by
``auth.require_writer`` or listed in ``UNGATED`` with the reason it changes nothing.
"""
import importlib
import os
import sys
import time
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from jose import jwt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import auth  # noqa: E402
import config  # noqa: E402
from routers import drift, feature_configs, inference, models, registered_models  # noqa: E402

USER = uuid.uuid4()
CID = str(uuid.uuid4())
MODEL_ID = "00000000-0000-4000-8000-000000000001"
CONFIG_ID = "00000000-0000-4000-8000-000000000002"
INTERNAL = "internal-service-token-for-tests"


class Reached(HTTPException):
    def __init__(self):
        super().__init__(status_code=418, detail="handler reached")


class Recorder:
    """Any downstream object: every attribute is an operation that records its call and stops."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def op(*args, **kwargs):
            self.calls.append(name)
            raise Reached()

        async def aop(*args, **kwargs):
            return op(*args, **kwargs)

        return aop


class FakePool:
    def __init__(self, row):
        self.row = row

    def acquire(self):
        row = self.row

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetchrow(self, sql, *args):
                return row

        return Conn()


def _row(role, is_superuser=False):
    return {"user_active": True, "company_id": uuid.UUID(CID), "company_active": True,
            "role": role, "is_superuser": is_superuser}


def _token(**claims):
    return jwt.encode({"sub": str(USER), "company_id": CID, "exp": int(time.time()) + 600, **claims},
                      config.config.JWT_SECRET_KEY, algorithm=auth.JWT_ALGORITHM)


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(config.config, "JWT_SECRET_KEY", "test-secret-of-adequate-length-0123456789")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL)


class NoSessionEnded:
    """A session store with no ended-session record (industryflow_contracts.sessions)."""

    async def get(self, key):
        return None


def _app(row, rec):
    app = FastAPI()
    for r in (models.router, registered_models.router, feature_configs.router, inference.router, drift.router):
        app.include_router(r)
    app.state.db_pool = FakePool(row)
    app.state.ml_repository = rec
    app.state.capability_store = rec
    app.state.session_store = NoSessionEnded()
    return TestClient(app, raise_server_exceptions=False)


# Every state-changing route reached with a user token, with a body that passes validation.
WRITES = [
    ("POST", "/api/models/upload-capability", None),
    ("POST", "/api/models", {"model_name": "m", "model_version": "1", "model_type": "anomaly"}),
    ("POST", f"/api/models/{MODEL_ID}/deploy", {"model_id": MODEL_ID, "environment": "production"}),
    ("DELETE", f"/api/models/{MODEL_ID}", None),
    ("PATCH", "/api/registered-models/pump", {"description": "d"}),
    ("POST", "/api/feature-configs", {"equipment_type": "p", "name": "n", "base_sensors": [],
                                      "transformations": []}),
    ("PUT", f"/api/feature-configs/{CONFIG_ID}", {"name": "n2"}),
    ("DELETE", f"/api/feature-configs/{CONFIG_ID}", None),
]
IDS = [f"{m} {p}" for m, p, _ in WRITES]


def _call(client, method, path, body, headers):
    return client.request(method, path, json=body, headers=headers)


def _sync_raise(rec, name):
    rec.calls.append(name)
    raise Reached()


@pytest.fixture
def downstream(monkeypatch):
    rec = Recorder()
    # The MLflow client the registered-models PATCH edits through (in a thread), and the first call
    # the registration handler makes: both are the handler body, past the gate.
    monkeypatch.setattr(registered_models, "_client", lambda: _sync_raise(rec, "client"))
    monkeypatch.setattr(models.uploaded_model, "provenance_of", lambda **kw: _sync_raise(rec, "provenance_of"))
    return rec


@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_an_observer_is_refused_every_write(method, path, body, downstream):
    r = _call(_app(_row("observer"), downstream), method, path, body, {"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 403, r.text
    assert "engineer or admin" in r.json()["detail"]
    assert downstream.calls == [], "the handler ran for an observer"


@pytest.mark.parametrize("row", [_row("engineer"), _row("admin"), _row("observer", is_superuser=True)],
                         ids=["engineer", "admin", "superuser"])
@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_a_writer_reaches_every_write(method, path, body, row, downstream):
    r = _call(_app(row, downstream), method, path, body, {"Authorization": f"Bearer {_token()}"})
    assert r.status_code not in (401, 403), r.text
    assert downstream.calls, f"the handler was not reached ({r.status_code} {r.text})"


@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_a_role_claimed_by_the_client_is_not_trusted(method, path, body, downstream):
    """An observer account is refused whatever its token or request says about its role."""
    headers = {"Authorization": f"Bearer {_token(role='admin', is_superuser=True)}",
               "X-Role": "admin", "X-Internal-Service-Token": "wrong"}
    r = _call(_app(_row("observer"), downstream), method, path, body, headers)
    assert r.status_code == 403, r.text
    assert downstream.calls == []


@pytest.mark.parametrize("method,path,body", WRITES, ids=IDS)
def test_a_write_without_a_token_is_401(method, path, body, downstream):
    r = _call(_app(_row("engineer"), downstream), method, path, body, {})
    assert r.status_code == 401, r.text
    assert downstream.calls == []


def test_an_account_with_no_role_is_refused(downstream):
    r = _call(_app(_row(None), downstream), "DELETE", f"/api/models/{MODEL_ID}", None,
              {"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 403


# --- reads stay open to observers; the internal path is unchanged -----------------------------------

READS = [
    ("/api/inference/predict", {"model_id": MODEL_ID, "sensor_data": {"s": 1.0}}),
    ("/api/drift/evaluate", {"model_id": MODEL_ID}),
]


@pytest.mark.parametrize("path,body", READS, ids=[p for p, _ in READS])
def test_an_observer_may_score_and_evaluate(path, body, downstream):
    r = _app(_row("observer"), downstream).post(path, json=body, headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code not in (401, 403), r.text
    assert downstream.calls == ["get_model_by_id"]


def test_an_observer_may_compare(downstream):
    r = _app(_row("observer"), downstream).post("/api/models/compare", json=[MODEL_ID],
                                                 headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code not in (401, 403), r.text
    assert downstream.calls == ["compare_models"]


@pytest.mark.parametrize("path,body", READS, ids=[p for p, _ in READS])
def test_the_internal_service_path_needs_no_user_token(path, body, downstream):
    """The alert worker's path: the internal token, the tenant from the body, no account read."""
    client = _app(None, downstream)  # no account row: a user-token read would 401
    r = client.post(path, json={**body, "company_id": CID}, headers={"X-Internal-Service-Token": INTERNAL})
    assert r.status_code not in (401, 403), r.text
    assert downstream.calls == ["get_model_by_id"]


# --- the guard: no non-GET route skips the role gate silently ---------------------------------------

# Non-GET routes that are deliberately not role-gated, each with why it changes nothing.
UNGATED = {
    ("POST", "/api/models/compare"): "read: compares stored metrics; POST only to carry a list of ids",
    ("POST", "/api/inference/predict"): "read: scores readings, writes nothing; also the internal-token path",
    ("POST", "/api/drift/evaluate"): "read: computes drift, writes nothing; also the internal-token path",
}
SAFE = {"GET", "HEAD", "OPTIONS"}


def _calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


@pytest.fixture
def unsafe_routes(monkeypatch, tmp_path):
    """(method, path) -> whether auth.require_writer is in its dependency tree, for every non-GET
    route of the real app.

    Since fastapi 0.138 ``app.routes`` holds included routers as opaque ``_IncludedRouter`` entries
    with no path or methods; iterating it directly sees none of this service's routes and passes
    vacuously. ``iter_route_contexts`` is what OpenAPI generation walks: every effective route, with
    its full path and the dependant it is served with.
    """
    monkeypatch.setattr(config.config, "MODEL_DIR", str(tmp_path))
    sys.modules.pop("main", None)
    app = importlib.import_module("main").app
    found = {}
    for ctx in iter_route_contexts(app.routes):
        for method in set(ctx.methods or ()) - SAFE:
            assert isinstance(ctx.original_route, APIRoute), f"{method} {ctx.path} is not an APIRoute"
            found[(method, ctx.path)] = auth.require_writer in set(_calls(ctx.dependant))
    assert ("POST", "/api/models") in found, f"the enumeration missed the app's routes: {sorted(found)}"
    return found


def test_every_non_get_route_is_role_gated_or_listed(unsafe_routes):
    ungated = [f"{m} {p}" for (m, p), gated in unsafe_routes.items() if not gated and (m, p) not in UNGATED]
    assert not ungated, (
        f"{ungated} change state without auth.require_writer: add "
        "dependencies=[Depends(auth.require_writer)], or list the route in UNGATED with why it is a read"
    )
    stale = set(UNGATED) - set(unsafe_routes)
    assert not stale, f"UNGATED lists routes the app does not have: {stale}"


def test_the_ungated_routes_are_not_gated(unsafe_routes):
    """UNGATED is the list of exceptions, not a second copy of the gated set."""
    gated = [k for k in UNGATED if unsafe_routes.get(k)]
    assert not gated, f"{gated} are gated; drop them from UNGATED"
