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
  * the gateway's writer itself (its decoding, binding and batch write) and the reader's SELECT run
    on the gateway's driver with the types the gateway binds (service_sql.py), and a row the
    database refuses is acknowledged without holding back its batch.

Requires a running database with the init scripts applied (the CI workflow stands one up via docker
compose). When no database is reachable it skips.
"""
import asyncio
import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

import service_sql  # noqa: E402

# Watched by db-tenant-isolation.yml's path filter (tests/config_guards/test_ci_path_filters.py).
DRAIN = "services/api_gateway/messaging/capability_audit_drain.py"
READER = "services/api_gateway/routers/capability_use.py"
ROLE = "api_gateway_user"


def _drain():
    """The gateway's writer module itself: its statements and its parameter building run here."""
    pytest.importorskip("sqlalchemy")
    pytest.importorskip("prometheus_client")
    pytest.importorskip("industryflow_contracts")
    spec = importlib.util.spec_from_file_location("capability_audit_drain", os.path.join(service_sql.ROOT, DRAIN))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drain = None


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
    global drain
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    c = conn.cursor()
    c.execute("SELECT 1 FROM pg_proc WHERE proname = 'record_capability_use'")
    if c.fetchone() is None:
        pytest.skip("record_capability_use() not present — init scripts not applied")
    drain = drain or _drain()
    c.execute(f"SET ROLE {ROLE}")
    try:
        yield c
    finally:
        c.execute("RESET ROLE")
        c.execute("DELETE FROM public.capability_use_events WHERE stream_id LIKE 'test-%'")
        conn.close()


def _event(**over):
    from industryflow_contracts import capability_audit as audit
    args = dict(kind="minted", plane="hub", company_id=str(uuid.uuid4()), user="alice", audience="sql",
                handle_id=str(uuid.uuid4()), detail="server=lab")
    args.update(over)
    kind = args.pop("kind")
    return audit.event(kind, **args)


def _stream_id():
    return f"test-{uuid.uuid4()}"


def _write(*entries):
    """The writer's own batch write, on the gateway's driver and as its role: the stream fields as a
    plane appended them, through the writer's decoding and parameter binding."""
    async def go():
        eng = service_sql.engine(ROLE)
        try:
            return await drain.write_batch(eng, list(entries))
        finally:
            await eng.dispose()
    return asyncio.run(go())


def _stored(cur, *stream_ids):
    cur.execute("SELECT stream_id FROM public.capability_use_events WHERE stream_id = ANY(%s) ORDER BY stream_id",
                (list(stream_ids),))
    return [r[0] for r in cur.fetchall()]


def test_the_writer_appends_once_per_stream_entry(cur):
    entry = (_stream_id(), _event())
    assert _write(entry) == [entry[0]]
    assert _write(entry) == [entry[0]]
    cur.execute("SELECT count(*) FROM public.capability_use_events WHERE stream_id = %s", (entry[0],))
    assert cur.fetchone()[0] == 1


def test_the_writer_binds_every_column_the_record_holds(cur):
    at = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    sid, fields = _stream_id(), _event(at=at, reason="x", digest="0123456789abcdef")
    _write((sid, fields))
    cur.execute("SELECT kind, plane, at, company_id::text, user_id, audience, handle_id::text, digest, reason, "
                "detail FROM public.capability_use_events WHERE stream_id = %s", (sid,))
    assert cur.fetchone() == ("minted", "hub", at, fields["company_id"], "alice", "sql", fields["handle_id"],
                              "0123456789abcdef", "x", "server=lab")


def test_a_row_the_record_refuses_is_acknowledged_without_holding_back_the_batch(cur, monkeypatch):
    # The contract refuses every value the record would, so the refusal is made past it: the real
    # driver's error must classify as a row fault, or one row would stall the record behind it.
    good, bad = (_stream_id(), _event()), (_stream_id(), _event())
    params = drain._params
    monkeypatch.setattr(drain, "_params", lambda sid, event: {**params(sid, event),
                                                              **({"audience": "admin"} if sid == bad[0] else {})})
    assert sorted(_write(good, bad)) == sorted([good[0], bad[0]])
    assert _stored(cur, good[0], bad[0]) == [good[0]]


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
    # Past the contract, which refuses these first: the writer's statement with the writer's binding.
    event = drain.audit.decode(_event())
    params = drain._params(_stream_id(), event)
    params.update(over)
    with pytest.raises(Exception) as refused:
        service_sql.run(service_sql.statement(DRAIN, "RECORD"), params, role=ROLE)
    assert service_sql.sqlstate(refused.value) == "23514"  # check_violation


def _read(**over):
    # The route's parameters, typed as FastAPI hands them over.
    params = {"company_id": None, "handle_id": None, "since": None, "before_id": None, "limit": 10}
    params.update(over)
    return service_sql.run(service_sql.statement(READER, "CAPABILITY_USE_SELECT"), params, role=ROLE)


def test_the_reader_scopes_to_one_tenant_and_filters_on_its_typed_parameters(cur):
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    first, second = _stream_id(), _stream_id()
    _write((first, _event(company_id=str(mine), at=datetime.now(timezone.utc) - timedelta(days=2))))
    _write((second, _event(company_id=str(mine))))
    _write((_stream_id(), _event(company_id=str(theirs), kind="refused", digest="0123456789abcdef",
                                 handle_id=None)))
    rows = _read(company_id=mine)
    assert [r["company_id"] for r in rows] == [mine, mine]
    assert [r["kind"] for r in _read(company_id=mine, since=datetime.now(timezone.utc) - timedelta(days=1))] \
        == ["minted"]
    newest = rows[0]["event_id"]
    assert len(_read(company_id=mine, before_id=newest)) == 1
    assert _read(company_id=mine, handle_id=rows[0]["handle_id"])[0]["event_id"] == newest


def test_retention_removes_only_what_is_older_than_its_period_and_never_under_the_floor(cur):
    old, new = (_stream_id(), _event()), (_stream_id(), _event())
    _write(old, new)
    cur.execute("RESET ROLE")
    cur.execute("UPDATE public.capability_use_events SET recorded_at = now() - interval '400 days' "
                "WHERE stream_id = %s", (old[0],))
    cur.execute(f"SET ROLE {ROLE}")

    async def expire(days):
        eng = service_sql.engine(ROLE)
        try:
            return await drain.expire(eng, days)
        finally:
            await eng.dispose()

    assert asyncio.run(expire(365)) >= 1
    assert _stored(cur, old[0], new[0]) == [new[0]]
    with pytest.raises(Exception) as refused:
        asyncio.run(expire(7))
    assert service_sql.sqlstate(refused.value) == "22023"  # invalid_parameter_value


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
