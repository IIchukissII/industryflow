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
from sqlalchemy.exc import IntegrityError, InvalidRequestError

import main
from capability_auth import suspended_key
from dependencies import get_db_with_tenant
from messaging import refresh_tokens
from routers import companies
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
    """The request's session, as get_db_with_tenant yields it: one transaction (session.begin()), so
    a statement after its commit is refused, as SQLAlchemy refuses it."""

    def __init__(self, active=True, fail_with=None, fail_request_with=None, deletion=None, steps=()):
        self.sql = []
        self.commits = 0
        self.committed = False
        self.active = active
        self.fail_with = fail_with
        self.fail_request_with = fail_request_with
        self.deletion = deletion
        self.steps = list(steps)
        self.user_ids = [uuid.uuid4(), uuid.uuid4()]

    async def execute(self, clause, params=None):
        if self.committed:
            raise InvalidRequestError("Can't operate on closed transaction inside context manager.")
        return await self._execute(clause, params)

    async def _execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        self.sql.append((sql, dict(params or {})))
        if self.fail_with and "provision_company" in sql:
            raise self.fail_with
        if "request_company_deletion" in sql:
            if self.fail_request_with:
                raise self.fail_request_with
            self.deletion = _Row(company_id=params["company_id"], requested_at=NOW,
                                 requested_by=params["actor"], completed_at=None)
            return _Result([self.deletion])
        if "FROM public.company_deletions" in sql:
            return _Result([self.deletion] if self.deletion else [])
        if "FROM public.company_deletion_steps" in sql:
            return _Result(self.steps)
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
        if sql.startswith('UPDATE public."user"') or sql.startswith('SELECT id FROM public."user"'):
            return _Result([_Row(id=i) for i in self.user_ids])
        return _Result([])

    async def commit(self):
        if self.committed:
            raise InvalidRequestError("Can't operate on closed transaction inside context manager.")
        self.committed = True
        self.commits += 1
        self.sql.append(("COMMIT", {}))

    def own_session(self):
        """A session of the route's own (companies._own_session), recorded in the same log."""
        return _OwnSession(self)


class _OwnSession:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _OwnTransaction(self._db)

    async def execute(self, clause, params=None):
        return await self._db._execute(clause, params)


class _OwnTransaction:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, *exc):
        if exc_type is None:
            self._db.commits += 1
            self._db.sql.append(("COMMIT", {}))
        return False


class FakeStore:
    def __init__(self):
        self.kv = {}

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(companies.redis_client, "redis", fake)
    return fake


@pytest.fixture
def as_user(monkeypatch, store):
    revoked = []

    async def revoke_all(user_id):
        # The suspension marker is in place before any session is ended.
        assert any(k.startswith("nbcap-suspended:") for k in store.kv)
        revoked.append(user_id)
        return 1

    monkeypatch.setattr(refresh_tokens, "end_all_sessions", revoke_all)

    def make(is_superuser=False, role="admin", db=None):
        db = db or FakeDB()

        async def _db():
            db.committed = False  # every request gets a session of its own
            yield db

        main.app.dependency_overrides[current_active_user] = lambda: SimpleNamespace(
            id=uuid.uuid4(), email="a@example.com", company_id=OWN, role=role,
            is_active=True, is_superuser=is_superuser)
        main.app.dependency_overrides[get_db_with_tenant] = _db
        monkeypatch.setattr(companies, "_own_session", db.own_session)
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


def test_delete_marks_the_company_suspended_for_every_capability_plane(as_user, store):
    c, _, _ = as_user(is_superuser=True)
    assert c.delete(f"/api/companies/{OTHER}").status_code == 200
    assert suspended_key(str(OTHER)) in store.kv


def test_deactivating_through_update_takes_the_delete_path(as_user, store):
    # ADR-0004 dec 2: deactivating a company ends its users' sessions, whatever the route.
    c, db, revoked = as_user(is_superuser=True)
    r = c.put(f"/api/companies/{OTHER}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    (sql, params), = _calls(db, "update_company")
    assert params["is_active"] is False
    assert revoked == [str(i) for i in db.user_ids]
    assert suspended_key(str(OTHER)) in store.kv
    assert db.commits == 2  # the suspension, then ending its SQL sessions


def test_reactivating_lifts_the_suspension_and_ends_nothing(as_user, store):
    store.kv[suspended_key(str(OTHER))] = "1"
    c, _, revoked = as_user(is_superuser=True, db=FakeDB(active=False))
    assert c.put(f"/api/companies/{OTHER}", json={"is_active": True}).status_code == 200
    assert suspended_key(str(OTHER)) not in store.kv
    assert revoked == []


def test_renaming_ends_no_session(as_user, store):
    c, _, revoked = as_user(is_superuser=True)
    assert c.put(f"/api/companies/{OTHER}", json={"company_name": "New"}).status_code == 200
    assert revoked == [] and store.kv == {}


def test_superuser_cannot_deactivate_own_company_through_update(as_user):
    c, db, revoked = as_user(is_superuser=True)
    assert c.put(f"/api/companies/{OWN}", json={"is_active": False}).status_code == 400
    assert db.sql == [] and revoked == []


def test_the_transition_is_seen_at_once_by_this_replica(as_user, company_flags):
    import asyncio

    import company_status

    assert asyncio.run(company_status.is_company_active(OTHER)) is True  # cached as active
    company_flags.inactive.add(str(OTHER))
    c, _, _ = as_user(is_superuser=True)
    assert c.put(f"/api/companies/{OTHER}", json={"is_active": False}).status_code == 200
    assert asyncio.run(company_status.is_company_active(OTHER)) is False


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


def test_open_sql_sessions_are_ended_after_the_suspension_commits(as_user):
    # NOLOGIN takes effect at commit, so the tenant's SQL sessions are ended in a later transaction.
    for method, body in (("put", {"is_active": False}), ("delete", None)):
        c, db, _ = as_user(is_superuser=True)
        r = c.request(method.upper(), f"/api/companies/{OTHER}", json=body)
        assert r.status_code == 200
        sqls = [sql for sql, _ in db.sql]
        suspend = next(i for i, sql in enumerate(sqls) if "update_company" in sql)
        (end, params), = [(i, p) for i, (sql, p) in enumerate(db.sql) if "end_suspended_tenant_sql_sessions" in sql]
        assert end > suspend and params == {"company_id": OTHER}
        assert db.commits == 2  # the suspension's commit, then the termination's own


def test_reactivation_and_rename_end_no_sql_session(as_user):
    c, db, _ = as_user(is_superuser=True, db=FakeDB(active=False))
    assert c.put(f"/api/companies/{OTHER}", json={"is_active": True, "company_name": "N"}).status_code == 200
    assert _calls(db, "end_suspended_tenant_sql_sessions") == []


def test_lifecycle_actions_leave_a_structured_record(as_user, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="routers.companies")
    c, _, _ = as_user(is_superuser=True)
    assert c.put(f"/api/companies/{OTHER}", json={"is_active": False}).status_code == 200
    records = [r.company_lifecycle for r in caplog.records if hasattr(r, "company_lifecycle")]
    assert records and records[-1]["action"] == "deactivate"
    assert records[-1]["company_id"] == str(OTHER) and records[-1]["actor"]


# --- the durable lifecycle record (ADR-0033 dec 6) -------------------------------------------------

def _records(db):
    return [(i, p) for i, (sql, p) in enumerate(db.sql) if "record_company_lifecycle" in sql]


@pytest.mark.parametrize("method, path, body, active, action", [
    ("POST", "/api/companies", {"company_name": "Acme"}, True, "create"),
    ("PUT", f"/api/companies/{OTHER}", {"is_active": False}, True, "deactivate"),
    ("PUT", f"/api/companies/{OTHER}", {"is_active": True}, False, "reactivate"),
    ("PUT", f"/api/companies/{OTHER}", {"company_name": "New"}, True, "rename"),
    ("DELETE", f"/api/companies/{OTHER}", None, True, "deactivate"),
])
def test_every_action_is_recorded_in_its_own_transaction(as_user, method, path, body, active, action):
    c, db, _ = as_user(is_superuser=True, db=FakeDB(active=active))
    assert c.request(method, path, json=body).status_code in (200, 201)
    [(at, params)] = _records(db)
    assert params["action"] == action and params["actor"]
    # Recorded inside the action's transaction: after the action's statement, before its commit.
    sqls = [sql for sql, _ in db.sql]
    acted = next(i for i, sql in enumerate(sqls) if "provision_company" in sql or "update_company" in sql)
    committed = sqls.index("COMMIT")
    assert acted < at < committed


# --- deletion request (ADR-0033 dec 4, 7) -----------------------------------------------------------

def test_superuser_requests_deletion_through_the_definer_function(as_user):
    c, db, _ = as_user(is_superuser=True, db=FakeDB(active=False))
    r = c.post(f"/api/companies/{OTHER}/deletion")
    assert r.status_code == 202
    assert r.json()["company_id"] == str(OTHER) and r.json()["completed_at"] is None
    (sql, params), = _calls(db, "request_company_deletion")
    assert params["company_id"] == OTHER and params["actor"]
    assert db.commits == 1
    # The request deletes nothing itself: the job does, store by store.
    assert not any("DROP" in s or "DELETE FROM" in s for s, _ in db.sql)


def test_tenant_admin_cannot_request_or_read_a_deletion(as_user):
    c, db, _ = as_user()
    assert c.post(f"/api/companies/{OWN}/deletion").status_code == 403
    assert c.get(f"/api/companies/{OWN}/deletion").status_code == 403
    assert db.sql == []


class _PgError(Exception):
    def __init__(self, sqlstate, hint=None):
        self.sqlstate, self.hint = sqlstate, hint


def _refusal(sqlstate, hint=None):
    orig = SimpleNamespace(sqlstate=sqlstate)
    orig.__cause__ = _PgError(sqlstate, hint)
    return IntegrityError("stmt", {}, orig)


@pytest.mark.parametrize("sqlstate, hint, status, detail", [
    ("P0002", None, 404, "Company not found"),
    ("55000", "not_suspended", 409, "Only a suspended company can be deleted"),
    ("55000", "suspended_too_recently", 409, "The company has not been suspended long enough to be deleted"),
    ("23505", None, 409, "A deletion of this company is already requested"),
])
def test_deletion_refusals_are_mapped(as_user, sqlstate, hint, status, detail):
    c, db, _ = as_user(is_superuser=True, db=FakeDB(fail_request_with=_refusal(sqlstate, hint)))
    r = c.post(f"/api/companies/{OTHER}/deletion")
    assert (r.status_code, r.json()["detail"]) == (status, detail)
    assert db.commits == 0


def test_a_company_with_a_deletion_request_cannot_be_changed(as_user):
    db = FakeDB(active=False)

    async def refuse(clause, params=None, _orig=db.execute):
        if "update_company" in str(clause):
            raise _refusal("55000", "deletion_requested")
        return await _orig(clause, params)

    db.execute = refuse
    c, _, _ = as_user(is_superuser=True, db=db)
    r = c.put(f"/api/companies/{OTHER}", json={"is_active": True})
    assert r.status_code == 409 and "deletion request" in r.json()["detail"]


def test_the_deletion_reports_each_store_removed(as_user):
    deletion = _Row(company_id=OTHER, requested_at=NOW, requested_by=OWN, completed_at=None)
    steps = [_Row(store="capabilities", completed_at=NOW, retained_until=None, detail={"handles": 3})]
    c, _, _ = as_user(is_superuser=True, db=FakeDB(deletion=deletion, steps=steps))
    r = c.get(f"/api/companies/{OTHER}/deletion")
    assert r.status_code == 200
    assert r.json()["steps"] == [{"store": "capabilities", "completed_at": NOW.isoformat().replace("+00:00", "Z"),
                                  "retained_until": None, "detail": {"handles": 3}}]


def test_no_deletion_requested_is_a_404(as_user):
    c, _, _ = as_user(is_superuser=True)
    assert c.get(f"/api/companies/{OTHER}/deletion").status_code == 404
