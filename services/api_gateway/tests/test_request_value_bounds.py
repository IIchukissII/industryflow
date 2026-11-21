# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A request value the database would refuse is refused at the route, 422, never a 500 from the database.

Every identifier the tenant tables hold is a UUID column, so a path or query parameter naming one is
typed UUID; every `limit` is bounded below by 1 (Postgres refuses a negative LIMIT) and above.
The guard walks the app's route table, so a new route is held to it without being listed.
"""
import types
import typing
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from dependencies import get_db_with_tenant, get_tenant_db, resolve_tenant_context
from test_write_roles import _http_routes
from users import current_active_user

# The identifiers held in UUID columns of the tenant tables and the registry.
UUID_PARAMS = {"equipment_id", "sensor_id", "rule_id", "model_id", "alert_id", "company_id", "user_id"}


def _params(route):
    dependant = route.dependant
    stack = [dependant]
    while stack:
        d = stack.pop()
        if d is None:
            continue
        yield from d.path_params
        yield from d.query_params
        stack.extend(d.dependencies)


def _base(annotation):
    """``Optional[X]`` and ``Annotated[X, ...]`` are ``X``."""
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if typing.get_origin(annotation) in (typing.Union, types.UnionType) and len(args) == 1:
        return _base(args[0])
    if typing.get_origin(annotation) is typing.Annotated:
        return _base(typing.get_args(annotation)[0])
    return annotation


def _routes():
    return list(_http_routes())


def test_every_identifier_parameter_is_a_uuid():
    wrong = sorted({f"{sorted(r.methods)[0]} {r.path} {p.name}: {p.field_info.annotation}"
                    for r in _routes() for p in _params(r)
                    if p.name in UUID_PARAMS and _base(p.field_info.annotation) is not uuid.UUID})
    assert wrong == []


def _bound(param, kind):
    for m in param.field_info.metadata:
        value = getattr(m, kind, None)
        if value is not None:
            return value
    return None


def test_every_limit_is_bounded_on_both_sides():
    wrong = []
    for r in _routes():
        for p in _params(r):
            if p.name != "limit":
                continue
            low = _bound(p, "ge") if _bound(p, "ge") is not None else _bound(p, "gt")
            high = _bound(p, "le") if _bound(p, "le") is not None else _bound(p, "lt")
            if low is None or low < 1 or high is None:
                wrong.append(f"{r.path} limit ge={low} le={high}")
    assert wrong == []


def test_the_guard_sees_the_routes_it_holds():
    names = {(r.path, p.name) for r in _routes() for p in _params(r)}
    assert ("/api/alerts", "limit") in names and ("/api/alerts", "equipment_id") in names
    assert ("/api/equipment/{equipment_id}", "equipment_id") in names


class NoDatabase:
    async def execute(self, *args, **kwargs):
        raise AssertionError("a refused value must not reach the database")


@pytest.fixture
def client():
    company = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), email="e@example.com", company_id=company, role="engineer",
                           is_superuser=False, is_active=True)

    async def _db():
        yield NoDatabase()

    main.app.dependency_overrides[current_active_user] = lambda: user
    main.app.dependency_overrides[get_db_with_tenant] = _db
    main.app.dependency_overrides[get_tenant_db] = _db
    main.app.dependency_overrides[resolve_tenant_context] = lambda: SimpleNamespace(company_id=str(company))
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.clear()


@pytest.mark.parametrize("path", [
    "/api/alerts?equipment_id=x", "/api/alerts?model_id=x", "/api/alerts?limit=-1",
    "/api/alerts/unacknowledged?limit=-1", "/api/alerts/critical?limit=-1",
    "/api/alerts/label-metrics?rule_id=x", "/api/equipment/x", "/api/measurements?equipment_id=x",
])
def test_a_malformed_value_is_refused_before_the_database(client, path):
    assert client.get(path).status_code == 422


@pytest.mark.parametrize("count", [0, -1])
def test_an_equipment_update_refuses_a_sensor_count_below_one(client, count):
    assert client.put(f"/api/equipment/{uuid.uuid4()}", json={"sensor_count": count}).status_code == 422
