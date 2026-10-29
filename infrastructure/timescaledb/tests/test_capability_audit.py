# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability-use record (init script 31, ADR-0042 dec 4, 6), against the real database, as the
API gateway's own role:

  * the gateway writes only through record_capability_use(), and a redelivered stream entry is a
    no-op,
  * no runtime role inserts, updates or deletes a row directly,
  * the closed lists are the database's too: an unknown kind, plane, audience or a long digest is
    refused,
  * the writer's statement and the reader's SELECT, read from the gateway's source and run here
    unchanged, write and read the record.

Requires a running database with the init scripts applied (the CI workflow stands one up via docker
compose). When no database is reachable it skips.
"""
import ast
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
# Watched by db-tenant-isolation.yml's path filter (tests/config_guards/test_ci_path_filters.py).
DRAIN = "services/api_gateway/messaging/capability_audit_drain.py"
READER = "services/api_gateway/routers/capability_use.py"


def _source_constant(path, name):
    tree = ast.parse(open(os.path.join(_ROOT, path)).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call):  # text("...") — the statement is its argument
                value = value.args[0]
            if isinstance(value, ast.JoinedStr):
                raise AssertionError(f"{name} in {path} is an f-string")
            return ast.literal_eval(value)
    raise AssertionError(f"{name} not found in {path}")


def _named(sql):
    """SQLAlchemy ``:name`` parameters as psycopg2 ``%(name)s``."""
    import re
    return re.sub(r"(?<!:):([a-z_]+)", r"%(\1)s", sql)


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


@pytest.fixture
def cur():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    c = conn.cursor()
    c.execute("SELECT 1 FROM pg_proc WHERE proname = 'record_capability_use'")
    if c.fetchone() is None:
        pytest.skip("record_capability_use() not present — init scripts not applied")
    c.execute("SET ROLE api_gateway_user")
    try:
        yield c
    finally:
        c.execute("RESET ROLE")
        c.execute("DELETE FROM public.capability_use_events WHERE stream_id LIKE 'test-%'")
        conn.close()


def _row(**over):
    row = {"stream_id": f"test-{uuid.uuid4()}", "kind": "minted", "plane": "hub",
           "at": "2026-10-29T12:00:00+00:00", "company_id": str(uuid.uuid4()), "user_id": "alice",
           "audience": "sql", "handle_id": str(uuid.uuid4()), "digest": None, "reason": None,
           "detail": "server=lab"}
    row.update(over)
    return row


def _record(cur, row):
    cur.execute(_named(_source_constant(DRAIN, "RECORD")), row)
    return cur.fetchone()[0]


def test_the_writer_appends_once_per_stream_entry(cur):
    row = _row()
    assert _record(cur, row) is True
    assert _record(cur, row) is False
    cur.execute("SELECT count(*) FROM public.capability_use_events WHERE stream_id = %s", (row["stream_id"],))
    assert cur.fetchone()[0] == 1


@pytest.mark.parametrize("sql", [
    "INSERT INTO public.capability_use_events (stream_id, kind, plane, at) VALUES ('test-x', 'minted', 'hub', now())",
    "UPDATE public.capability_use_events SET reason = 'x'",
    "DELETE FROM public.capability_use_events",
])
def test_no_runtime_role_changes_the_record_directly(cur, sql):
    with pytest.raises(errors.InsufficientPrivilege):
        cur.execute(sql)


@pytest.mark.parametrize("over", [
    {"kind": "used"}, {"plane": "kernel"}, {"audience": "admin"}, {"digest": "0" * 64},
])
def test_the_closed_lists_hold_in_the_database(cur, over):
    with pytest.raises(errors.CheckViolation):
        _record(cur, _row(**over))


def test_the_reader_scopes_to_one_tenant(cur):
    mine, theirs = str(uuid.uuid4()), str(uuid.uuid4())
    _record(cur, _row(company_id=mine))
    _record(cur, _row(company_id=theirs, kind="refused", digest="0123456789abcdef", handle_id=None))
    select = _named(_source_constant(READER, "CAPABILITY_USE_SELECT"))
    cur.execute(select, {"company_id": mine, "handle_id": None, "since": None, "before_id": None, "limit": 10})
    rows = cur.fetchall()
    assert len(rows) == 1 and str(rows[0][4]) == mine


def test_retention_removes_only_what_is_older_than_its_period_and_never_under_the_floor(cur):
    old, new = _row(), _row()
    _record(cur, old)
    _record(cur, new)
    cur.execute("RESET ROLE")
    cur.execute("UPDATE public.capability_use_events SET recorded_at = now() - interval '400 days' "
                "WHERE stream_id = %s", (old["stream_id"],))
    cur.execute("SET ROLE api_gateway_user")
    cur.execute(_named(_source_constant(DRAIN, "EXPIRE")), {"days": 365})
    assert cur.fetchone()[0] >= 1
    cur.execute("SELECT stream_id FROM public.capability_use_events WHERE stream_id IN (%s, %s)",
                (old["stream_id"], new["stream_id"]))
    assert [r[0] for r in cur.fetchall()] == [new["stream_id"]]
    with pytest.raises(errors.InvalidParameterValue):
        cur.execute(_named(_source_constant(DRAIN, "EXPIRE")), {"days": 7})


def test_the_roles_a_kernels_sql_session_runs_as_cannot_read_the_record():
    # The record names every tenant's users and handles (ADR-0042 dec 6): a kernel's SQL session runs
    # as its tenant's proxy login and reader role, and neither reads it.
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    c = conn.cursor()
    c.execute("SELECT 1 FROM pg_proc WHERE proname = 'record_capability_use'")
    if c.fetchone() is None:
        pytest.skip("init scripts not applied")
    company_id = uuid.uuid4()
    c.execute("SELECT create_tenant_schema(%s, %s)", (str(company_id), f"test-{company_id}"))
    schema = c.fetchone()[0]
    suffix = str(company_id).replace("-", "_")
    try:
        c.execute("SELECT rolname FROM pg_roles WHERE rolname IN (%s, %s)",
                  (f"tenant_reader_{suffix}", f"tenant_sqlproxy_{suffix}"))
        roles = [r[0] for r in c.fetchall()]
        assert f"tenant_reader_{suffix}" in roles
        for role in roles:
            c.execute(f'SET ROLE "{role}"')
            with pytest.raises(errors.InsufficientPrivilege):
                c.execute("SELECT 1 FROM public.capability_use_events LIMIT 1")
            c.execute("RESET ROLE")
    finally:
        c.execute("RESET ROLE")
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        c.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        c.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        conn.close()
