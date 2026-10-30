# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A service's SQL, read from its source and run on the driver that service runs it on.

A statement proved through another driver proves less than it seems: psycopg2 sends parameters as
untyped text, which the server casts, while asyncpg binds each one as the type the server infers and
refuses a Python value of another type. A string bound for a ``timestamptz`` passed the psycopg2
proof and failed every write on the box (#502); a string bound for an ``interval`` did the same to an
endpoint before it. So the proofs here never choose a driver: the service a statement comes from
does (``DRIVERS``), and tests/config_guards/test_service_sql_drivers.py holds that table to each
service's runtime pins. The caller passes the parameters with the Python types the service binds.

Every source path read here is in db-tenant-isolation.yml's path filter
(tests/config_guards/test_ci_path_filters.py), so a change to the statement runs its proof.
"""
from __future__ import annotations

import ast
import asyncio
import os
import ssl as ssl_mod
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Union

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

ASYNCPG = "asyncpg"
SQLALCHEMY_ASYNCPG = "sqlalchemy+asyncpg"
PSYCOPG2 = "psycopg2"

# The driver each service runs its SQL on.
DRIVERS = {
    "services/api_gateway/": SQLALCHEMY_ASYNCPG,
    "services/alert_service/": ASYNCPG,
    "services/ml_service/": ASYNCPG,
    "services/spark_jobs/": PSYCOPG2,
}


@dataclass(frozen=True)
class Statement:
    path: str
    name: str
    sql: str
    driver: str


def literal(path: str, name: str) -> Any:
    """The module-level constant ``name`` exactly as ``path`` holds it (a string, ``text("…")`` or a
    literal container of strings)."""
    for node in ast.parse(open(os.path.join(ROOT, path)).read()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call):  # text("...") — the statement is its argument
                value = value.args[0]
            if isinstance(value, ast.JoinedStr):
                raise AssertionError(f"{name} in {path} is an f-string")
            return ast.literal_eval(value)
    raise AssertionError(f"{name} not found in {path}")


def statement(path: str, name: str, sql: Optional[str] = None) -> Statement:
    """The statement ``name`` in ``path``, on its service's driver; ``sql`` when the service composes
    it from constants read with ``literal``."""
    driver = next((d for prefix, d in DRIVERS.items() if path.startswith(prefix)), None)
    if driver is None:
        raise AssertionError(f"{path}: no driver recorded for its service in DRIVERS")
    return Statement(path, name, literal(path, name) if sql is None else sql, driver)


def _ssl():
    # CI uses a plain loopback DB (no PGSSLMODE); a TLS-enforced DB (ADR-0017) sets PGSSLMODE.
    mode = (os.getenv("PGSSLMODE") or "").lower()
    if mode in ("require", "prefer", "allow"):
        ctx = ssl_mod.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl_mod.CERT_NONE
        return ctx
    if mode in ("verify-ca", "verify-full"):
        return ssl_mod.create_default_context(cafile=os.getenv("PGSSLROOTCERT") or None)
    return None


def _settings():
    return dict(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        database=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
    )


def engine(role: str):
    """An SQLAlchemy asyncpg engine, as the gateway builds one, whose every connection runs as ``role``."""
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from sqlalchemy.ext.asyncio import create_async_engine
    s = _settings()
    url = sqlalchemy.engine.URL.create("postgresql+asyncpg", username=s["user"], password=s["password"],
                                       host=s["host"], port=s["port"], database=s["database"])
    eng = create_async_engine(url, connect_args={"ssl": _ssl()}, poolclass=sqlalchemy.pool.NullPool)

    @sqlalchemy.event.listens_for(eng.sync_engine, "connect")
    def _as_role(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f'SET ROLE "{role}"')
        cursor.close()

    return eng


Params = Union[Sequence[Any], Mapping[str, Any]]


async def _asyncpg(stmt, params, role, search_path):
    asyncpg = pytest.importorskip("asyncpg")
    s = _settings()
    conn = await asyncio.wait_for(asyncpg.connect(ssl=_ssl(), **s), timeout=5)
    try:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            if search_path:
                await conn.execute("SELECT set_config('search_path', $1, true)", search_path)
            return [dict(r) for r in await conn.fetch(stmt.sql, *params)]
    finally:
        await conn.close()


async def _sqlalchemy(stmt, params, role, search_path):
    from sqlalchemy import text
    eng = engine(role)
    try:
        async with eng.begin() as conn:
            if search_path:
                await conn.execute(text("SELECT set_config('search_path', :p, true)"), {"p": search_path})
            result = await conn.execute(text(stmt.sql), dict(params))
            return [dict(r._mapping) for r in result.fetchall()] if result.returns_rows else []
    finally:
        await eng.dispose()


def _psycopg2(stmt, params, role, search_path):
    psycopg2 = pytest.importorskip("psycopg2")
    s = _settings()
    conn = psycopg2.connect(host=s["host"], port=s["port"], dbname=s["database"], user=s["user"],
                            password=s["password"], connect_timeout=5)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f'SET LOCAL ROLE "{role}"')
            if search_path:
                cur.execute("SELECT set_config('search_path', %s, true)", (search_path,))
            cur.execute(stmt.sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def run(stmt: Statement, params: Params = (), *, role: str, search_path: Optional[str] = None) -> List[dict]:
    """Run ``stmt`` once, in its own committed transaction, as ``role``, on its service's driver; the
    rows as dicts. A refusal raises that driver's own error (``sqlstate`` reads its code)."""
    if stmt.driver == ASYNCPG:
        return asyncio.run(_asyncpg(stmt, params, role, search_path))
    if stmt.driver == SQLALCHEMY_ASYNCPG:
        return asyncio.run(_sqlalchemy(stmt, params, role, search_path))
    return _psycopg2(stmt, params, role, search_path)


def sqlstate(exc: BaseException) -> Optional[str]:
    """The SQLSTATE of a refusal from any of the three drivers, through SQLAlchemy's wrapping."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if code:
            return code
        exc = getattr(exc, "orig", None) or exc.__cause__
    return None
