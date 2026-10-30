# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Platform switches (ADR-0043): only a superuser reads or flips one, the flip goes through the
database's function with the user as its actor, and a switch the database does not know is a 404.

The function itself is proven against the real database in
infrastructure/timescaledb/tests/test_platform_switch_control.py."""
import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import asyncpg
import pytest
from fastapi import HTTPException
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError

import dependencies
from routers import platform_switches as ps

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def _refusal(error):
    """``error`` as SQLAlchemy's asyncpg adapter raises it."""
    dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
    translated = dbapi._asyncpg_error_translate[asyncpg.PostgresError](str(error))
    translated.pgcode = translated.sqlstate = error.sqlstate
    translated.__cause__ = error
    return DBAPIError.instance("SELECT ...", {}, translated, dbapi.Error)


class Db:
    def __init__(self, row=None, fail=None):
        self.calls, self.row, self.fail, self.committed, self.rolled_back = [], row, fail, False, False

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if self.fail:
            raise self.fail
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: self.row, all=lambda: []))

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.parametrize("user", [
    SimpleNamespace(is_superuser=False, role="admin"),
    SimpleNamespace(is_superuser=False, role="engineer"),
])
def test_no_tenant_role_passes(user):
    with pytest.raises(HTTPException) as e:
        asyncio.run(dependencies.require_superuser(user))
    assert e.value.status_code == 403


def test_a_superuser_passes():
    root = SimpleNamespace(is_superuser=True)
    assert asyncio.run(dependencies.require_superuser(root)) is root


def test_every_route_takes_the_superuser_gate():
    for route in ps.router.routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert dependencies.require_superuser in deps, route.path


def test_a_flip_goes_through_the_function_with_the_user_as_its_actor():
    root = SimpleNamespace(is_superuser=True, id=uuid.uuid4())
    db = Db(row={"changed": True, "value": False, "updated_at": NOW})
    out = asyncio.run(ps.flip_switch("stateful_features_enabled", ps.Flip(value=False), user=root, db=db))
    (sql, params), = db.calls
    assert "public.set_platform_switch(" in sql
    assert params == {"key": "stateful_features_enabled", "value": "false", "actor_id": root.id}
    assert db.committed and (out.changed, out.value) == (True, False)


def test_a_switch_the_database_does_not_know_is_not_found():
    root = SimpleNamespace(is_superuser=True, id=uuid.uuid4())
    db = Db(fail=_refusal(asyncpg.exceptions.InvalidParameterValueError("unknown platform switch x")))
    with pytest.raises(HTTPException) as e:
        asyncio.run(ps.flip_switch("x", ps.Flip(value=True), user=root, db=db))
    assert e.value.status_code == 404 and db.rolled_back


def test_any_other_failure_is_not_disguised():
    root = SimpleNamespace(is_superuser=True, id=uuid.uuid4())
    db = Db(fail=_refusal(asyncpg.exceptions.InsufficientPrivilegeError("permission denied")))
    with pytest.raises(DBAPIError):
        asyncio.run(ps.flip_switch("stateful_features_enabled", ps.Flip(value=True), user=root, db=db))


@pytest.mark.parametrize("value", ["off", "false", 0, 1, None])
def test_a_value_of_another_type_is_refused_before_the_database(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ps.Flip(value=value)
