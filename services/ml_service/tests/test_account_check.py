# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ML service re-reads the account behind every token (ADR-0004 dec 2, ADR-0033 dec 3).

A validly signed access token is not enough: a deactivated or deleted user, or a user of a suspended
company, is refused at once rather than when the token expires, and so is a token whose session has
ended (industryflow_contracts.sessions). The account store is a fake pool, the session store a
mapping read the way the contract reads it (only GETs).
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
COMPANY = uuid.uuid4()


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


class Records:
    """The session store as the verifier reads it: GETs over the contract's keys."""

    def __init__(self, **kv):
        self.kv = kv

    async def get(self, key):
        return self.kv.get(key)


def _client(pool, store="fresh"):
    app = FastAPI()

    @app.get("/who")
    async def who(user: dict = Depends(auth.verify_jwt_token)):
        return {"user": user["user_id"]}

    if pool is not None:
        app.state.db_pool = pool
    if store is not None:
        app.state.session_store = Records() if store == "fresh" else store
    return TestClient(app)


def _token(sub=USER, company=COMPANY, **claims):
    if company is not None:
        claims["company_id"] = str(company)
    return jwt.encode({"sub": str(sub), "exp": int(time.time()) + 600, **claims},
                      config.config.JWT_SECRET_KEY, algorithm=auth.JWT_ALGORITHM)


def _get(pool, token=None, store="fresh"):
    return _client(pool, store).get("/who", headers={"Authorization": f"Bearer {token or _token()}"})


def _row(user_active=True, company_active=True, company_id=COMPANY, role="observer", is_superuser=False):
    return {"user_active": user_active, "company_id": company_id, "company_active": company_active,
            "role": role, "is_superuser": is_superuser}


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


def test_the_role_is_the_account_s_not_the_token_s():
    """The role gate reads the role on the account row, re-read per request; a role claim in the
    token (or anything else the client sends) is not consulted."""
    app = FastAPI()

    @app.get("/who")
    async def who(user: dict = Depends(auth.verify_jwt_token)):
        return {"role": user["role"], "is_superuser": user["is_superuser"]}

    app.state.db_pool = FakePool(_row(role="observer"))
    app.state.session_store = Records()
    token = jwt.encode({"sub": str(USER), "company_id": str(COMPANY), "role": "admin", "is_superuser": True,
                        "exp": int(time.time()) + 600},
                       config.config.JWT_SECRET_KEY, algorithm=auth.JWT_ALGORITHM)
    r = TestClient(app).get("/who", headers={"Authorization": f"Bearer {token}"})
    assert r.json() == {"role": "observer", "is_superuser": False}


def test_a_user_with_no_company_is_not_refused_for_it():
    assert _get(FakePool(_row(company_id=None, company_active=None)), _token(company=None)).status_code == 200


def test_a_token_whose_subject_is_not_a_user_id_is_refused():
    pool = FakePool(_row())
    assert _get(pool, _token(sub="service")).status_code == 401
    assert pool.queries == []


@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
def test_a_token_signed_with_another_algorithm_is_refused(algorithm):
    token = jwt.encode({"sub": str(USER), "exp": int(time.time()) + 600},
                       config.config.JWT_SECRET_KEY, algorithm=algorithm)
    pool = FakePool(_row())
    assert _get(pool, token).status_code == 401
    assert pool.queries == []


def test_an_unreadable_account_store_fails_closed():
    import asyncpg

    assert _get(FakePool(error=asyncpg.PostgresError("down"))).status_code == 503
    assert _get(None).status_code == 503


def test_the_registry_read_is_granted():
    sql = open(os.path.join(ROOT, "infrastructure", "timescaledb", "init-scripts",
                            "21-company-provisioning.sql"), encoding="utf-8").read()
    assert "GRANT SELECT ON public.companies TO ml_service_user;" in sql


def _store(**records):
    """A session store holding the gateway's ended-session records, keyed through the contract."""
    from industryflow_contracts import sessions

    kv = {}
    if "ended_session" in records:
        kv[sessions.ended_session_key(records["ended_session"])] = b"1"
    if "all_ended_at" in records:
        kv[sessions.user_ended_key(str(USER))] = repr(records["all_ended_at"]).encode()
    return Records(**kv)


def test_a_token_of_a_session_ended_by_logout_is_refused():
    now = int(time.time())
    store = _store(ended_session="s-1")
    assert _get(FakePool(_row()), _token(sid="s-1", iat=now), store).status_code == 401
    assert _get(FakePool(_row()), _token(sid="s-2", iat=now), store).status_code == 200


def test_ending_all_sessions_refuses_the_tokens_issued_before_and_not_after():
    now = int(time.time())
    store = _store(all_ended_at=now - 10)
    assert _get(FakePool(_row()), _token(sid="s", iat=now - 20), store).status_code == 401
    assert _get(FakePool(_row()), _token(sid="s", iat=now - 10), store).status_code == 401
    assert _get(FakePool(_row()), _token(sid="s", iat=now), store).status_code == 200
    # A token with no issue time cannot be placed after the end.
    assert _get(FakePool(_row()), _token(), store).status_code == 401


def test_an_unreadable_session_store_fails_closed():
    class Down:
        async def get(self, key):
            raise ConnectionError("down")

    assert _get(FakePool(_row()), store=Down()).status_code == 503
    assert _get(FakePool(_row()), store=None).status_code == 503


def test_the_session_store_is_required_configuration():
    assert "SESSION_REDIS_URL" in open(os.path.join(ROOT, "services", "ml_service", "api", "config.py"),
                                       encoding="utf-8").read().split("def validate", 1)[1]


def test_a_token_naming_another_tenant_than_the_account_is_refused():
    """A user moved to another company: the tokens issued before name the old one."""
    pool = FakePool(_row(company_id=uuid.uuid4()))
    assert _get(pool).status_code == 401
    assert _get(FakePool(_row()), _token(company=None)).status_code == 401
    assert _get(FakePool(_row(company_id=None, company_active=None))).status_code == 401
    assert _get(FakePool(_row()), _token(company=str(COMPANY).upper())).status_code == 200
