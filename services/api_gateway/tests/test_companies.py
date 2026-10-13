# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Company provisioning goes through the registry's SECURITY DEFINER functions (ADR-0004 dec 5).

The gateway role may only read public.companies, so every write calls provision_company() or
update_company() (21-company-provisioning.sql; their DB-side behaviour is proven in
infrastructure/timescaledb/tests/test_company_provisioning.py). These tests drive the routes with a
fake session and check who may do what, and what each route asks the database to do.
"""
import datetime
import os
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import main
from dependencies import get_db_with_tenant
from messaging import refresh_tokens
from users import current_active_user

OWN = uuid.uuid4()
OTHER = uuid.uuid4()
NOW = datetime.datetime(2026, 10, 13, tzinfo=datetime.timezone.utc)


class _Row(SimpleNamespace):
    pass


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, active=True, fail_with=None):
        self.sql = []
        self.commits = 0
        self.active = active
        self.fail_with = fail_with
        self.user_ids = [uuid.uuid4(), uuid.uuid4()]

    async def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        self.sql.append((sql, dict(params or {})))
        if self.fail_with and "provision_company" in sql:
            raise self.fail_with
        if "provision_company" in sql or "update_company" in sql:
            cid = (params or {}).get("company_id", OWN)
            name = (params or {}).get("name") or "Acme"
            active = (params or {}).get("is_active")
            if "update_company" in sql and "false" in sql:
                active = False
            return _Result([_Row(company_id=cid, company_name=name,
                                 is_active=self.active if active is None else active, created_at=NOW)])
        if sql.startswith("SELECT is_active FROM public.companies"):
            return _Result([_Row(is_active=self.active)])
        if sql.startswith('UPDATE public."user"'):
            return _Result([_Row(id=i) for i in self.user_ids])
        return _Result([])

    async def commit(self):
        self.commits += 1


@pytest.fixture
def as_user(monkeypatch):
    revoked = []

    async def revoke_all(user_id):
        revoked.append(user_id)
        return 1

    monkeypatch.setattr(refresh_tokens, "end_all_sessions", revoke_all)

    def make(is_superuser=False, role="admin", db=None):
        db = db or FakeDB()

        async def _db():
            yield db

        main.app.dependency_overrides[current_active_user] = lambda: SimpleNamespace(
            id=uuid.uuid4(), email="a@example.com", company_id=OWN, role=role,
            is_active=True, is_superuser=is_superuser)
        main.app.dependency_overrides[get_db_with_tenant] = _db
        return TestClient(main.app, raise_server_exceptions=False), db, revoked

    yield make
    main.app.dependency_overrides.clear()


def _calls(db, fn):
    return [(s, p) for s, p in db.sql if fn in s]


def test_superuser_provisions_through_the_definer_function(as_user):
    c, db, _ = as_user(is_superuser=True)
    r = c.post("/api/companies", json={"company_name": "Acme"})
    assert r.status_code == 201
    assert r.json()["company_name"] == "Acme"
    (sql, params), = _calls(db, "provision_company")
    assert params == {"name": "Acme", "is_active": True}
    assert db.commits == 1


def test_tenant_admin_cannot_provision(as_user):
    c, db, _ = as_user()
    assert c.post("/api/companies", json={"company_name": "Acme"}).status_code == 403
    assert db.sql == []


@pytest.mark.parametrize("sqlstate, status", [("23505", 409), ("23514", 422), ("42501", 500)])
def test_database_refusals_are_mapped(as_user, sqlstate, status):
    err = IntegrityError("stmt", {}, SimpleNamespace(sqlstate=sqlstate))
    c, db, _ = as_user(is_superuser=True, db=FakeDB(fail_with=err))
    r = c.post("/api/companies", json={"company_name": "Acme"})
    assert r.status_code == status
    assert db.commits == 0


@pytest.mark.parametrize("name", ["", "x" * 256])
def test_invalid_name_never_reaches_the_database(as_user, name):
    c, db, _ = as_user(is_superuser=True)
    assert c.post("/api/companies", json={"company_name": name}).status_code == 422
    assert db.sql == []


def test_tenant_admin_renames_own_company_even_with_the_flag_echoed(as_user):
    c, db, _ = as_user(db=FakeDB(active=True))
    r = c.put(f"/api/companies/{OWN}", json={"company_name": "New", "is_active": True})
    assert r.status_code == 200
    (sql, params), = _calls(db, "update_company")
    assert params["name"] == "New" and params["is_active"] is None


def test_tenant_admin_cannot_change_activation(as_user):
    c, db, _ = as_user(db=FakeDB(active=True))
    assert c.put(f"/api/companies/{OWN}", json={"company_name": "New", "is_active": False}).status_code == 403
    assert _calls(db, "update_company") == []


def test_tenant_admin_cannot_touch_another_company(as_user):
    c, db, _ = as_user()
    assert c.put(f"/api/companies/{OTHER}", json={"company_name": "x"}).status_code == 404
    assert db.sql == []


def test_delete_deactivates_company_and_users_and_ends_sessions(as_user):
    c, db, revoked = as_user(is_superuser=True)
    r = c.delete(f"/api/companies/{OTHER}")
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    (sql, params), = _calls(db, "update_company")
    assert "NULL, false" in sql and params == {"company_id": OTHER}
    (sql, params), = _calls(db, 'UPDATE public."user"')
    assert "is_active = false" in sql and params == {"company_id": OTHER}
    assert revoked == [str(i) for i in db.user_ids]
    assert not any("DELETE" in s or "DROP" in s for s, _ in db.sql)


def test_tenant_admin_cannot_delete(as_user):
    c, db, revoked = as_user()
    assert c.delete(f"/api/companies/{OWN}").status_code == 403
    assert db.sql == [] and revoked == []


def test_superuser_cannot_deactivate_own_company(as_user):
    c, db, _ = as_user(is_superuser=True)
    assert c.delete(f"/api/companies/{OWN}").status_code == 400
    assert db.sql == []


def test_no_route_writes_the_registry_directly():
    # The gateway role may not, so a direct INSERT/UPDATE/DELETE here would only ever fail at runtime.
    path = os.path.join(os.path.dirname(__file__), "..", "routers", "companies.py")
    src = open(path, encoding="utf-8").read()
    assert not re.search(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+public\.companies", src, re.I)
