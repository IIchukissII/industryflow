# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The first-run superuser and the password change it is held to (ADR-0004 dec 11).

The bootstrap's decisions run on a session that answers its statements; the statements themselves run
for real at every compose-smoke start on an empty platform (compose-smoke.yml)."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi_users.exceptions import InvalidPasswordException
from fastapi_users.password import PasswordHelper

import first_run
import users
from messaging import redis_client as redis_module


class Session:
    """Answers the bootstrap's reads; records its writes."""

    def __init__(self, accounts=False, tenant=None):
        self.accounts, self.tenant, self.writes, self.committed = accounts, tenant, [], False

    async def execute(self, clause, params=None):
        sql = str(clause)
        if "pg_advisory_xact_lock" in sql:
            return SimpleNamespace(first=lambda: (None,))
        if sql == 'SELECT 1 FROM public."user" LIMIT 1':
            return SimpleNamespace(first=lambda: (1,) if self.accounts else None)
        if "FROM public.companies WHERE is_active" in sql:
            return SimpleNamespace(first=lambda: SimpleNamespace(company_id=self.tenant) if self.tenant else None)
        if "provision_company" in sql:
            self.writes.append(("provision", params))
            return SimpleNamespace(first=lambda: SimpleNamespace(company_id=uuid.UUID(int=7)))
        self.writes.append((sql, params))
        return SimpleNamespace(first=lambda: None)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


def _inserted(session):
    return next(p for sql, p in session.writes if sql.startswith("INSERT"))


@pytest.mark.asyncio
async def test_nothing_is_created_on_a_platform_that_has_any_account():
    """Not only a superuser: a platform seeded with tenant admins alone, or whose last superuser was
    removed, must not gain one with a published password."""
    session = Session(accounts=True, tenant=uuid.uuid4())
    assert await first_run.ensure_first_superuser(session, None, None, None) is None
    assert session.writes == [] and not session.committed


@pytest.mark.asyncio
async def test_the_defaults_join_the_oldest_tenant_and_must_change_the_password():
    tenant = uuid.uuid4()
    session = Session(tenant=tenant)
    created = await first_run.ensure_first_superuser(session, None, None, None)
    row = _inserted(session)
    assert created["email"] == first_run.DEFAULT_EMAIL and created["password_change_required"] is True
    assert row["company_id"] == tenant and row["forced"] is True
    assert PasswordHelper().verify_and_update(first_run.DEFAULT_PASSWORD, row["hashed"])[0]
    assert not any(sql == "provision" for sql, _ in session.writes) and session.committed
    assert not any("record_company_lifecycle" in sql for sql, _ in session.writes)


@pytest.mark.asyncio
async def test_with_no_tenant_one_is_provisioned_and_its_creation_recorded():
    session = Session()
    created = await first_run.ensure_first_superuser(session, None, None, "Acme")
    assert created["tenant_provisioned"] and ("provision", {"name": "Acme"}) in session.writes
    recorded = [p for sql, p in session.writes if "record_company_lifecycle" in sql]
    assert recorded and recorded[0]["actor"] == _inserted(session)["id"] and "first_run" in recorded[0]["detail"]


@pytest.mark.asyncio
async def test_a_password_the_deployment_chose_is_not_forced():
    session = Session(tenant=uuid.uuid4())
    created = await first_run.ensure_first_superuser(session, " Ops@Example.com ", "Chosen-by-the-operator", None)
    assert created["email"] == "ops@example.com" and created["password_change_required"] is False
    assert _inserted(session)["forced"] is False


# --- the gate ----------------------------------------------------------------------------------

class _Manager:
    def __init__(self, user):
        self.user = user

    def parse_id(self, value):
        return uuid.UUID(value)

    async def get(self, user_id):
        return self.user


@pytest.fixture
def store(monkeypatch):
    class NoSessionEnded:
        async def get(self, key):
            return None

    monkeypatch.setattr(redis_module.redis_client, "redis", NoSessionEnded())


_DEFAULT_HASH = PasswordHelper().hash("industryflow")


def _held():
    return SimpleNamespace(id=uuid.uuid4(), email="held@example.com", company_id=None, is_active=True, role="admin",
                           hashed_password=_DEFAULT_HASH, password_change_required=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_", [("GET", "/api/companies"), ("POST", "/api/users"), ("GET", "/auth/verify"),
                                      ("GET", "/internal/dashboards/assertion"), None])
async def test_a_held_account_reaches_nothing_else(store, request_):
    user = _held()
    strategy = users.get_jwt_strategy()
    token = await strategy.write_token(user, "sid-held")
    marker = users.CURRENT_REQUEST.set(request_)
    try:
        with pytest.raises(HTTPException) as refused:
            await strategy.read_token(token, _Manager(user))
    finally:
        users.CURRENT_REQUEST.reset(marker)
    assert refused.value.status_code == 403 and refused.value.detail == users.PASSWORD_CHANGE_REQUIRED


@pytest.mark.asyncio
@pytest.mark.parametrize("request_", sorted(users.PASSWORD_CHANGE_ALLOWED))
async def test_a_held_account_reads_itself_changes_its_password_and_signs_out(store, request_):
    user = _held()
    strategy = users.get_jwt_strategy()
    token = await strategy.write_token(user, "sid-held")
    marker = users.CURRENT_REQUEST.set(request_)
    try:
        assert await strategy.read_token(token, _Manager(user)) is user
    finally:
        users.CURRENT_REQUEST.reset(marker)


def test_only_reading_oneself_changing_the_password_and_signing_out_are_allowed():
    assert users.PASSWORD_CHANGE_ALLOWED == {("GET", "/users/me"), ("PATCH", "/users/me"),
                                             ("POST", "/auth/logout"), ("POST", "/auth/jwt/logout")}


@pytest.mark.asyncio
async def test_the_password_cannot_be_changed_to_itself():
    manager = users.UserManager(user_db=None)
    user = SimpleNamespace(password_change_required=True, hashed_password=PasswordHelper().hash("industryflow"))
    for same in ("industryflow", "Industryflow", " industryflow ", "INDUSTRYFLOW"):
        with pytest.raises(InvalidPasswordException):
            await manager.validate_password(same, user)
    chosen = SimpleNamespace(password_change_required=True, hashed_password=PasswordHelper().hash("Chosen-one-1"))
    with pytest.raises(InvalidPasswordException):
        await manager.validate_password("Chosen-one-1", chosen)
    await manager.validate_password("Something-else-entirely", user)
    # An ordinary account may set any password the rules allow, its current one included.
    await manager.validate_password("industryflow", SimpleNamespace(password_change_required=False,
                                                                    hashed_password=user.hashed_password))


@pytest.mark.asyncio
async def test_the_new_password_releases_the_account_in_the_same_write():
    writes = []

    class UserDb:
        async def update(self, user, values):
            writes.append(values)
            return user

        async def get_by_email(self, email):
            return None

    manager = users.UserManager(user_db=UserDb())
    await manager._update(_held(), {"password": "Something-else-entirely"})
    (values,) = writes
    assert values["password_change_required"] is False and "hashed_password" in values
    writes.clear()
    await manager._update(_held(), {"email": "x@example.com"})
    assert "password_change_required" not in writes[0]


def test_a_self_chosen_password_has_the_admin_forms_minimum():
    import schemas
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        schemas.UserSelfUpdate(password="short")
    assert schemas.UserSelfUpdate(password="long-enough").password == "long-enough"


def test_the_cli_takes_the_first_run_lock():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import user_admin_db

    assert user_admin_db.FIRST_RUN_LOCK_KEY == first_run.LOCK_KEY


def test_through_the_app_a_held_account_reads_itself_and_nothing_else(store):
    """The request reaches authentication through main.remember_the_request: without it every request
    of a held account would be refused, and with it broken any would pass."""
    import asyncio

    from fastapi.testclient import TestClient

    import main
    from users import get_user_manager

    user = _held()
    user.company_id = uuid.uuid4()
    user.is_superuser, user.is_verified = True, False
    main.app.dependency_overrides[get_user_manager] = lambda: _Manager(user)
    try:
        token = asyncio.run(users.get_jwt_strategy().write_token(user, "sid-app"))
        client = TestClient(main.app, raise_server_exceptions=False)
        bearer = {"Authorization": f"Bearer {token}"}
        me = client.get("/users/me", headers=bearer)
        assert me.status_code == 200 and me.json()["password_change_required"] is True, me.text
        refused = client.get("/api/companies", headers=bearer)
        assert refused.status_code == 403 and refused.json()["detail"] == users.PASSWORD_CHANGE_REQUIRED
        assert client.get("/auth/verify", headers=bearer).status_code == 403
    finally:
        main.app.dependency_overrides.clear()
