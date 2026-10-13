# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ML service re-reads the account behind every token (ADR-0004 dec 2 rev 2, ADR-0033 dec 3).

A validly signed access token is not enough: a deactivated or deleted user, or a user of a suspended
company, is refused at once rather than when the token expires. The account store is a fake pool.
"""
import os
import sys
import time
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import auth  # noqa: E402
import config  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
USER = uuid.uuid4()


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(config.config, "JWT_SECRET_KEY", "test-secret-of-adequate-length-0123456789")


class FakePool:
    def __init__(self, row=None, error=None):
        self.row, self.error, self.queries = row, error, []

    def acquire(self):
        pool = self

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetchrow(self, sql, *args):
                pool.queries.append((sql, args))
                if pool.error:
                    raise pool.error
                return pool.row

        return Conn()


def _client(pool):
    app = FastAPI()

    @app.get("/who")
    async def who(user: dict = Depends(auth.verify_jwt_token)):
        return {"user": user["user_id"]}

    if pool is not None:
        app.state.db_pool = pool
    return TestClient(app)


def _token(sub=USER):
    return jwt.encode({"sub": str(sub), "exp": int(time.time()) + 600},
                      config.config.JWT_SECRET_KEY, algorithm=config.config.JWT_ALGORITHM)


def _get(pool, token=None):
    return _client(pool).get("/who", headers={"Authorization": f"Bearer {token or _token()}"})


def _row(user_active=True, company_active=True, company_id=uuid.uuid4()):
    return {"user_active": user_active, "company_id": company_id, "company_active": company_active}


def test_an_active_user_of_an_active_company_passes():
    pool = FakePool(_row())
    r = _get(pool)
    assert r.status_code == 200 and r.json() == {"user": str(USER)}
    (sql, args), = pool.queries
    assert args == (USER,) and "public.companies" in sql


@pytest.mark.parametrize("row", [
    None,                              # deleted
    _row(user_active=False),           # deactivated
    _row(company_active=False),        # company suspended
    _row(company_active=None),         # company missing from the registry
])
def test_an_unusable_account_is_refused_at_once(row):
    assert _get(FakePool(row)).status_code == 401


def test_a_user_with_no_company_is_not_refused_for_it():
    assert _get(FakePool(_row(company_id=None, company_active=None))).status_code == 200


def test_a_token_whose_subject_is_not_a_user_id_is_refused():
    pool = FakePool(_row())
    assert _get(pool, _token(sub="service")).status_code == 401
    assert pool.queries == []


def test_an_unreadable_account_store_fails_closed():
    import asyncpg

    assert _get(FakePool(error=asyncpg.PostgresError("down"))).status_code == 503
    assert _get(None).status_code == 503


def test_the_registry_read_is_granted():
    sql = open(os.path.join(ROOT, "infrastructure", "timescaledb", "init-scripts",
                            "21-company-provisioning.sql"), encoding="utf-8").read()
    assert "GRANT SELECT ON public.companies TO ml_service_user;" in sql
