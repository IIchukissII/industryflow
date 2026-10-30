# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
observer is read-only (docs/operations/user-management.md; ADR-0004 dec 5).

Every route that writes tenant data (equipment, sensors, alert rules, alert acknowledgement and
labels) needs a writer role: engineer or admin. The app is driven with the tenant session
replaced by a fake that records every statement, so a refused write is proven to touch nothing.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import main
from dependencies import get_db_with_tenant, get_writer_with_company
from users import current_active_user

EQ, SENSOR, RULE, ALERT = (str(uuid.uuid4()) for _ in range(4))

WRITES = [
    ("post", "/api/equipment", {"equipment_type": "pump", "name": "p", "sensor_count": 1}),
    ("put", f"/api/equipment/{EQ}", {"name": "p2"}),
    ("delete", f"/api/equipment/{EQ}", None),
    ("post", f"/api/equipment/{EQ}/sensors", {"sensor_name": "s", "sensor_type": "t", "position": 1}),
    ("patch", f"/api/equipment/{EQ}/sensors/{SENSOR}", {"display_name": "s"}),
    ("delete", f"/api/equipment/{EQ}/sensors/{SENSOR}", None),
    ("post", f"/api/equipment/{EQ}/sensors/bulk", {"sensors": []}),
    ("post", "/api/alert-rules", {"name": "n", "severity": "low"}),
    ("put", f"/api/alert-rules/{RULE}", {"threshold": 1.0}),
    ("patch", f"/api/alert-rules/{RULE}/detection-mode", {"detection_type": "threshold"}),
    ("delete", f"/api/alert-rules/{RULE}", None),
    ("patch", f"/api/alerts/{ALERT}/acknowledge", None),
    ("patch", f"/api/alerts/{ALERT}/label", {"label": "true_positive"}),
]

# Write routes outside the tenant-data writer gate, each with its own authorization:
# authentication, self-service, user administration (admin), tenant provisioning and platform switches
# (superuser).
OWN_AUTHORIZATION = {
    "/auth/jwt/login", "/auth/jwt/logout", "/auth/login", "/auth/refresh", "/auth/logout",
    "/users/me", "/api/users", "/api/users/{user_id}",
    "/api/companies", "/api/companies/{company_id}", "/api/companies/{company_id}/deletion",
    "/api/platform/switches/{key}",
}


class RecordingSession:
    def __init__(self):
        self.statements = []

    async def execute(self, clause, params=None):
        self.statements.append(str(clause))
        raise AssertionError("a refused write must not reach the database")

    async def commit(self):
        raise AssertionError("a refused write must not commit")


@pytest.fixture
def as_role():
    session = RecordingSession()

    async def _db():
        yield session

    def make(role, is_superuser=False):
        main.app.dependency_overrides[current_active_user] = lambda: SimpleNamespace(
            id=uuid.uuid4(), email=f"{role}@example.com", company_id=uuid.uuid4(), role=role,
            is_active=True, is_superuser=is_superuser)
        main.app.dependency_overrides[get_db_with_tenant] = _db
        return TestClient(main.app, raise_server_exceptions=False), session

    yield make
    main.app.dependency_overrides.clear()


@pytest.mark.parametrize("method, path, body", WRITES)
def test_observer_cannot_write(as_role, method, path, body):
    c, session = as_role("observer")
    r = c.request(method.upper(), path, json=body)
    assert r.status_code == 403
    assert session.statements == []


@pytest.mark.parametrize("role", ["engineer", "admin"])
@pytest.mark.parametrize("method, path, body", WRITES)
def test_writer_roles_pass_the_gate(as_role, role, method, path, body):
    c, _ = as_role(role)
    r = c.request(method.upper(), path, json=body)
    # Past the gate the fake session refuses to run (500) or the body fails validation (422);
    # what matters here is only that the role was not the reason.
    assert r.status_code != 403


def test_superuser_passes_the_gate(as_role):
    c, _ = as_role("observer", is_superuser=True)
    method, path, body = WRITES[0]
    assert c.request(method.upper(), path, json=body).status_code != 403


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def _http_routes():
    """Every HTTP route the app serves. fastapi >= 0.138 wraps included routers, so app.routes
    holds wrappers whose effective routes carry the full path, methods and dependant."""
    for route in main.app.routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        elif isinstance(route, APIRoute):
            yield route


def test_route_walk_sees_the_included_routers():
    # The guard below is only as good as its walk: it must see the routers' routes.
    paths = {r.path for r in _http_routes()}
    assert "/api/equipment" in paths and "/api/alerts/{alert_id}/acknowledge" in paths


def test_every_tenant_write_route_is_gated():
    # Guard: a new write route cannot quietly skip the writer gate. Each one either depends on
    # get_writer_with_company or is listed above with its own authorization.
    ungated = []
    gated_paths = set()
    for route in _http_routes():
        if not set(route.methods or ()) & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        if route.path in OWN_AUTHORIZATION:
            continue
        if _depends_on(route.dependant, get_writer_with_company):
            gated_paths.add(route.path)
        else:
            ungated.append(f"{sorted(route.methods)} {route.path}")
    assert ungated == []
    # ...and the list driven above covers every gated route.
    driven = {p for _, p, _ in WRITES}
    templated = {p.replace(EQ, "{equipment_id}").replace(SENSOR, "{sensor_id}")
                 .replace(RULE, "{rule_id}").replace(ALERT, "{alert_id}") for p in driven}
    assert gated_paths <= templated
