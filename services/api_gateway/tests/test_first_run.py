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

    def __init__(self, superuser=False, tenant=None):
        self.superuser, self.tenant, self.writes, self.committed = superuser, tenant, [], False

    async def execute(self, clause, params=None):
        sql = str(clause)
        if "pg_advisory_xact_lock" in sql:
            return SimpleNamespace(first=lambda: (None,))
        if 'FROM public."user" WHERE is_superuser' in sql:
            return SimpleNamespace(first=lambda: (1,) if self.superuser else None)
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
async def test_nothing_is_created_while_a_superuser_exists():
    session = Session(superuser=True, tenant=uuid.uuid4())
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


def _held():
    return SimpleNamespace(id=uuid.uuid4(), company_id=None, is_active=True, role="admin", hashed_password="h",
                           password_change_required=True)


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
    with pytest.raises(InvalidPasswordException):
        await manager.validate_password("industryflow", user)
    await manager.validate_password("Something-else-entirely", user)
    # An ordinary account may set any password the rules allow, its current one included.
    await manager.validate_password("industryflow", SimpleNamespace(password_change_required=False,
                                                                    hashed_password=user.hashed_password))


@pytest.mark.asyncio
async def test_changing_the_password_releases_the_account(monkeypatch):
    updates = []

    class UserDb:
        async def update(self, user, values):
            updates.append(values)

    async def no_op(*a, **k):
        return None

    from messaging import refresh_tokens
    monkeypatch.setattr(refresh_tokens, "end_all_sessions", no_op)
    monkeypatch.setattr(users, "_clear_sign_in_failures", no_op)
    manager = users.UserManager(user_db=UserDb())
    await manager.on_after_update(_held(), {"password": "new"})
    assert updates == [{"password_change_required": False}]
    updates.clear()
    await manager.on_after_update(_held(), {"email": "x@example.com"})
    assert updates == []
