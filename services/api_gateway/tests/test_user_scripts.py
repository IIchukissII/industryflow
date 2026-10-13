# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
scripts/manage_users.py and scripts/seed_users.py (docs/operations/user-management.md).

They run with this service's dependencies and connect as its role, so they are tested here. The
database is a fake that records every statement with its parameters: the checks are that user
input only ever travels as a bound parameter, that superuser is never implied by a role, that a
password is never an argument, that the connection verifies the server like the gateway does,
and that every failure exits non-zero.
"""
import io
import os
import re
import ssl
import sys
from contextlib import asynccontextmanager

import pytest

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts"))
sys.path.insert(0, SCRIPTS)

import manage_users  # noqa: E402
import seed_users  # noqa: E402
import user_admin_db  # noqa: E402

COMPANY = "6b1d2c3e-4f50-4a61-8b72-9c83d4e5f601"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeConn:
    """Answers the handful of statements the scripts issue."""

    def __init__(self, companies=(COMPANY,), users=(), fail=None):
        self.companies = set(companies)
        self.users = set(users)
        self.fail = fail
        self.statements = []

    async def execute(self, clause, params=None):
        sql = " ".join(str(clause).split())
        params = dict(params or {})
        self.statements.append((sql, params))
        if self.fail:
            raise self.fail
        if sql.startswith("SELECT 1 FROM public.companies"):
            return _Result([(1,)] if str(params["company_id"]) in self.companies else [])
        if sql.startswith('SELECT 1 FROM public."user"'):
            return _Result([(1,)] if params["email"] in self.users else [])
        if sql.startswith('INSERT INTO public."user"'):
            self.users.add(params["email"])
            return _Result([])
        if sql.startswith(("DELETE", "UPDATE")):
            return _Result([("id",)] if params["email"] in self.users else [])
        if sql.startswith("SELECT u.email"):
            return _Result([("a@example.com", "Acme", "admin", True, False, True)])
        return _Result([])


@pytest.fixture
def db(monkeypatch):
    conn = FakeConn()

    @asynccontextmanager
    async def connect():
        yield conn

    monkeypatch.setattr(user_admin_db, "connect", connect)
    monkeypatch.setenv("API_GATEWAY_DB_PASSWORD", "unused-by-the-fake")
    return conn


def _stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _inserted(conn):
    return [p for s, p in conn.statements if s.startswith('INSERT INTO public."user"')]


def test_create_binds_every_value_and_is_not_superuser(db, monkeypatch):
    email = "o'brien@example.com"  # would have broken (or injected into) the old f-string SQL
    _stdin(monkeypatch, "s3cret-pw\n")
    rc = manage_users.main(["create", "--email", email, "--company-id", COMPANY, "--role", "admin",
                            "--password-stdin"])
    assert rc == 0
    (params,) = _inserted(db)
    assert params["email"] == email and str(params["company_id"]) == COMPANY
    assert params["is_superuser"] is False
    assert params["hashed_password"] != "s3cret-pw"
    assert user_admin_db.PasswordHelper().verify_and_update("s3cret-pw", params["hashed_password"])[0]
    for sql, _ in db.statements:
        assert email not in sql and "s3cret-pw" not in sql


def test_superuser_only_with_the_flag(db, monkeypatch):
    _stdin(monkeypatch, "pw-1\n")
    assert manage_users.main(["create", "--email", "s@example.com", "--company-id", COMPANY,
                              "--role", "observer", "--superuser", "--password-stdin"]) == 0
    assert _inserted(db)[0]["is_superuser"] is True


def test_password_is_never_an_argument():
    with pytest.raises(SystemExit) as e:
        manage_users.build_parser().parse_args(
            ["create", "--email", "x@example.com", "--company-id", COMPANY, "--role", "admin", "--password", "pw"])
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        manage_users.build_parser().parse_args(["reset-password", "--email", "x@example.com", "--password", "pw"])


def test_prompted_password_must_match(db, monkeypatch):
    answers = iter(["one", "two"])
    monkeypatch.setattr(manage_users, "getpass", lambda prompt: next(answers))
    assert manage_users.main(["create", "--email", "m@example.com", "--company-id", COMPANY, "--role", "admin"]) == 1
    assert _inserted(db) == []


@pytest.mark.parametrize("argv", [
    ["create", "--email", "x@example.com", "--company-id", "not-a-uuid", "--role", "admin", "--password-stdin"],
    ["create", "--email", "x@example.com", "--company-id", "00000000-0000-4000-8000-000000000000", "--role", "admin",
     "--password-stdin"],
    ["create", "--email", "not-an-email", "--company-id", COMPANY, "--role", "admin", "--password-stdin"],
    ["reset-password", "--email", "nobody@example.com", "--password-stdin"],
    ["delete", "--email", "nobody@example.com", "--yes"],
])
def test_failures_exit_nonzero(db, monkeypatch, argv):
    _stdin(monkeypatch, "pw\n")
    assert manage_users.main(argv) == 1
    assert _inserted(db) == []


def test_database_error_exits_nonzero(db, monkeypatch):
    db.fail = RuntimeError("relation does not exist")
    assert manage_users.main(["list"]) == 1


def test_list_joins_the_real_companies_table(db, capsys):
    assert manage_users.main(["list"]) == 0
    (sql, _), = db.statements
    assert "LEFT JOIN public.companies c ON c.company_id = u.company_id" in sql
    assert "c.company_name" in sql
    assert "Acme" in capsys.readouterr().out


def test_reset_password_binds_the_new_hash(db, monkeypatch):
    db.users.add("u@example.com")
    _stdin(monkeypatch, "new-pw\n")
    assert manage_users.main(["reset-password", "--email", "u@example.com", "--password-stdin"]) == 0
    (sql, params), = [(s, p) for s, p in db.statements if s.startswith("UPDATE")]
    assert params["email"] == "u@example.com" and "new-pw" not in sql
    assert user_admin_db.PasswordHelper().verify_and_update("new-pw", params["hashed"])[0]


def test_ci_runs_these_tests_when_the_scripts_change():
    # The scripts live outside services/, so the unit-test path filter must name each of them.
    wf = open(os.path.join(SCRIPTS, "..", ".github", "workflows", "unit-tests.yml"), encoding="utf-8").read()
    for name in ("manage_users.py", "seed_users.py", "user_admin_db.py"):
        assert f"- 'scripts/{name}'" in wf, name


def test_no_script_formats_sql():
    # Every statement is a text() with bound parameters; none is built with an f-string or %.
    for name in ("manage_users.py", "seed_users.py", "user_admin_db.py"):
        src = open(os.path.join(SCRIPTS, name), encoding="utf-8").read()
        assert not re.search(r"execute\(\s*f[\"']", src), name
        assert not re.search(r"text\(\s*f[\"']", src), name
        assert "session.execute(\"" not in src and "session.execute('" not in src, name


def test_connection_verifies_the_server_like_the_gateway(monkeypatch):
    captured = {}

    class FakeEngine:
        def begin(self):
            @asynccontextmanager
            async def cm():
                yield "conn"
            return cm()

        async def dispose(self):
            pass

    def fake_create(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeEngine()

    monkeypatch.setattr(user_admin_db, "create_async_engine", fake_create)
    monkeypatch.setenv("API_GATEWAY_DB_PASSWORD", "p@ss/word:with#chars")
    monkeypatch.delenv("DB_SSLMODE", raising=False)

    import asyncio

    async def go():
        async with user_admin_db.connect() as conn:
            return conn

    assert asyncio.run(go()) == "conn"
    ctx = captured["connect_args"]["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True  # verify-full
    assert captured["url"].password == "p@ss/word:with#chars"


# --- seed_users -----------------------------------------------------------------------------

def _seed_env(monkeypatch, **users):
    for k in list(os.environ):
        if k.startswith("ADMIN_USER_"):
            monkeypatch.delenv(k)
    for k, v in users.items():
        monkeypatch.setenv(k, v)


def test_seed_admin_is_not_superuser_unless_asked(db, monkeypatch):
    _seed_env(monkeypatch,
              ADMIN_USER_1_EMAIL="a@example.com", ADMIN_USER_1_PASSWORD="pw-a", ADMIN_USER_1_COMPANY_ID=COMPANY,
              ADMIN_USER_2_EMAIL="b@example.com", ADMIN_USER_2_PASSWORD="pw-b", ADMIN_USER_2_COMPANY_ID=COMPANY,
              ADMIN_USER_2_SUPERUSER="true")
    assert seed_users.main() == 0
    by_email = {p["email"]: p for p in _inserted(db)}
    assert by_email["a@example.com"]["is_superuser"] is False
    assert by_email["a@example.com"]["role"] == "admin"
    assert by_email["b@example.com"]["is_superuser"] is True


def test_seed_skips_existing_users(db, monkeypatch):
    db.users.add("a@example.com")
    _seed_env(monkeypatch, ADMIN_USER_1_EMAIL="a@example.com", ADMIN_USER_1_PASSWORD="pw",
              ADMIN_USER_1_COMPANY_ID=COMPANY)
    assert seed_users.main() == 0
    assert _inserted(db) == []


@pytest.mark.parametrize("env", [
    {},
    {"ADMIN_USER_1_EMAIL": "a@example.com"},
    {"ADMIN_USER_1_EMAIL": "a@example.com", "ADMIN_USER_1_PASSWORD": "pw",
     "ADMIN_USER_1_COMPANY_ID": "00000000-0000-4000-8000-000000000000"},
    {"ADMIN_USER_1_EMAIL": "a@example.com", "ADMIN_USER_1_PASSWORD": "pw",
     "ADMIN_USER_1_COMPANY_ID": COMPANY, "ADMIN_USER_1_ROLE": "root"},
])
def test_seed_failures_exit_nonzero(db, monkeypatch, env):
    _seed_env(monkeypatch, **env)
    assert seed_users.main() == 1
    assert _inserted(db) == []
