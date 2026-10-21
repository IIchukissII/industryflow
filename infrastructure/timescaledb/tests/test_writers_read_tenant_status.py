# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The writers of tenant data can tell a suspended tenant from an active one (24-writers-read-tenant-status.sql).

The stream jobs and the alert worker refuse a tenant the registry does not hold as active (ADR-0033
dec 3). Without the grant their registry read fails, and the batch fails with it, so this runs each
writer's own registry read as that writer's role against a real tenant before and after suspension,
and checks the grant reaches nothing else of the registry.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one
up); skips when none is reachable.
"""
import ast
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

WRITERS = ("spark_streaming_user", "alert_service_user")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _source_sql(path: str, name: str) -> str:
    """A statement exactly as the writer's source holds it. Each source is in db-tenant-isolation.yml's
    path filter, so a change to it runs this proof."""
    for node in ast.parse(open(os.path.join(_ROOT, path)).read()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


STREAM_READ = _source_sql("services/spark_jobs/reading_guard.py", "TENANT_STATUS_SELECT")
# asyncpg's positional parameter, run here through psycopg2.
ALERT_LISTING = _source_sql("services/alert_service/worker/repository.py", "TENANT_SCHEMAS_SELECT").replace("$1", "%s")


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


def _as(cur, role, sql, args=()):
    cur.execute(f'SET ROLE "{role}"')
    try:
        cur.execute(sql, args)
        return cur.fetchall()
    finally:
        cur.execute("RESET ROLE")


@pytest.fixture(scope="module")
def tenant():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'provision_company'")
    if cur.fetchone() is None:
        pytest.skip("provision_company() not present — init scripts not applied")
    [(company_id, *_)] = _as(cur, "api_gateway_user", "SELECT * FROM public.provision_company(%s)",
                             (f"writers-status-{uuid.uuid4().hex[:8]}",))
    schema = f"tenant_{str(company_id).replace('-', '_')}"
    yield cur, company_id, schema
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{schema[len("tenant_"):]}"')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{schema[len("tenant_"):]}"')
    cur.execute("DELETE FROM public.companies WHERE company_id = %s", (str(company_id),))
    conn.close()


def _suspend(cur, company_id, active):
    _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, NULL, %s)", (str(company_id), active))


@pytest.mark.parametrize("role", WRITERS)
def test_a_writer_reads_whether_a_tenant_is_active(tenant, role):
    cur, company_id, schema = tenant
    assert _as(cur, role, STREAM_READ, (schema,)) == [(True,)]
    _suspend(cur, company_id, False)
    try:
        assert _as(cur, role, STREAM_READ, (schema,)) == [(False,)]
    finally:
        _suspend(cur, company_id, True)


def test_the_alert_worker_lists_a_tenant_only_while_it_is_active(tenant):
    cur, company_id, schema = tenant
    like = "tenant\\_%"
    assert (schema,) in _as(cur, "alert_service_user", ALERT_LISTING, (like,))
    _suspend(cur, company_id, False)
    try:
        assert (schema,) not in _as(cur, "alert_service_user", ALERT_LISTING, (like,))
    finally:
        _suspend(cur, company_id, True)


@pytest.mark.parametrize("role", WRITERS)
def test_a_writer_reads_nothing_else_of_the_registry(tenant, role):
    cur, _, _ = tenant
    with pytest.raises(errors.InsufficientPrivilege):
        _as(cur, role, "SELECT company_name FROM public.companies")
