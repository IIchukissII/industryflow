# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
User self-service and administration stay inside the caller's tenant (ADR-0003 dec 1, ADR-0004 dec 4-5).

The real app is driven with the session and the user manager replaced by in-memory fakes, so each
assertion is about the route's authorization, not about persistence.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from fastapi_users import exceptions

import main
from routers import user_admin
from users import current_active_user, get_user_manager

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


def _user(company_id, role="observer", is_superuser=False, email=None):
    return SimpleNamespace(
        id=uuid.uuid4(), email=email or f"{uuid.uuid4().hex[:8]}@example.com", company_id=company_id,
        role=role, is_active=True, is_superuser=is_superuser, is_verified=False, hashed_password="x",
    )


class FakeManager:
    def __init__(self, users):
        self.users = {u.id: u for u in users}

    async def get(self, user_id):
        if user_id not in self.users:
            raise exceptions.UserNotExists()
        return self.users[user_id]

    async def create(self, user_create, safe=False, request=None):
        d = user_create.create_update_dict() if safe else user_create.create_update_dict_superuser()
        d.pop("password")
        u = _user(d.pop("company_id"), email=d.pop("email"))
        for k, v in d.items():
            setattr(u, k, v)
        self.users[u.id] = u
        return u

    async def update(self, user_update, user, safe=False, request=None):
        d = user_update.create_update_dict() if safe else user_update.create_update_dict_superuser()
        d.pop("password", None)
        for k, v in d.items():
            setattr(user, k, v)
        return user

    async def delete(self, user, request=None):
        del self.users[user.id]


@pytest.fixture
def world():
    admin_a = _user(TENANT_A, role="admin")
    member_a = _user(TENANT_A)
    member_b = _user(TENANT_B)
    super_a = _user(TENANT_A, role="admin", is_superuser=True)
    manager = FakeManager([admin_a, member_a, member_b, super_a])
    state = SimpleNamespace(caller=admin_a, manager=manager, admin_a=admin_a, member_a=member_a,
                            member_b=member_b, super_a=super_a)
    main.app.dependency_overrides[current_active_user] = lambda: state.caller
    main.app.dependency_overrides[get_user_manager] = lambda: manager
    yield state
    main.app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_public_registration_is_gone(client):
    r = client.post("/auth/register", json={"email": "x@example.com", "password": "pw-12345678",
                                            "company_id": str(TENANT_B), "role": "admin"})
    assert r.status_code in (404, 405)


def test_user_admin_routes_require_a_session(client):
    assert client.get("/api/users").status_code == 401
    assert client.post("/api/users", json={}).status_code == 401
    assert client.patch(f"/api/users/{uuid.uuid4()}", json={}).status_code == 401
    assert client.patch("/users/me", json={}).status_code == 401


@pytest.mark.parametrize("field, value", [("company_id", str(TENANT_B)), ("role", "admin"),
                                          ("is_superuser", True)])
def test_self_update_cannot_change_tenant_role_or_flags(world, client, field, value):
    world.caller = world.member_a
    r = client.patch("/users/me", json={field: value})
    assert r.status_code == 422
    assert world.member_a.company_id == TENANT_A
    assert world.member_a.role == "observer" and world.member_a.is_superuser is False


def test_self_update_changes_own_email(world, client):
    world.caller = world.member_a
    r = client.patch("/users/me", json={"email": "new@example.com"})
    assert r.status_code == 200 and world.member_a.email == "new@example.com"


@pytest.mark.parametrize("role", ["observer", "engineer"])
def test_non_admin_cannot_list_users(world, client, role):
    world.member_a.role = role
    world.caller = world.member_a
    assert client.get("/api/users").status_code == 403


def test_admin_lists_only_own_tenant(world, client, monkeypatch):
    seen = {}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, statement, params=None):
            seen["sql"], seen["params"] = str(statement), params
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))

    monkeypatch.setattr(user_admin, "AsyncSessionLocal", Session)
    assert client.get("/api/users").status_code == 200
    assert "WHERE company_id = :company_id" in seen["sql"]
    assert seen["params"] == {"company_id": TENANT_A}


def test_non_admin_cannot_create_users(world, client):
    world.caller = world.member_a
    assert client.post("/api/users", json={"email": "n@example.com", "password": "pw-12345678"}).status_code == 403


def test_admin_creates_only_into_own_tenant(world, client):
    ok = client.post("/api/users", json={"email": "n@example.com", "password": "pw-12345678", "role": "engineer",
                                         "company_id": str(TENANT_A), "is_superuser": False})
    assert ok.status_code == 201 and ok.json()["company_id"] == str(TENANT_A)
    other = client.post("/api/users", json={"email": "m@example.com", "password": "pw-12345678",
                                            "company_id": str(TENANT_B)})
    assert other.status_code == 403
    su = client.post("/api/users", json={"email": "s@example.com", "password": "pw-12345678", "is_superuser": True})
    assert su.status_code == 403


def test_admin_cannot_move_a_user_to_another_tenant(world, client):
    r = client.patch(f"/api/users/{world.member_a.id}", json={"company_id": str(TENANT_B)})
    assert r.status_code == 403 and world.member_a.company_id == TENANT_A


def test_admin_cannot_touch_another_tenants_user(world, client):
    assert client.patch(f"/api/users/{world.member_b.id}", json={"role": "observer"}).status_code == 404
    assert client.delete(f"/api/users/{world.member_b.id}").status_code == 404
    assert world.member_b.id in world.manager.users


def test_admin_cannot_modify_a_superuser(world, client):
    r = client.patch(f"/api/users/{world.super_a.id}", json={"password": "pw-12345678"})
    assert r.status_code == 403


def test_admin_edits_own_tenant_user_with_unchanged_fields_echoed(world, client):
    """The admin form sends company_id and is_superuser back unchanged; that is not a privilege request."""
    r = client.patch(f"/api/users/{world.member_a.id}", json={
        "email": world.member_a.email, "role": "engineer", "company_id": str(TENANT_A), "is_superuser": False,
        "is_active": True})
    assert r.status_code == 200 and world.member_a.role == "engineer"


def test_superuser_may_move_a_user(world, client):
    world.caller = world.super_a
    r = client.patch(f"/api/users/{world.member_b.id}", json={"company_id": str(TENANT_A)})
    assert r.status_code == 200 and world.member_b.company_id == TENANT_A
