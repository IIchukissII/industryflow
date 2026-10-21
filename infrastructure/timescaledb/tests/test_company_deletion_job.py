# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tenant-delete job's database interface (26-company-deletion-job.sql, ADR-0033 dec 4, 8).

The database is emptied last and only once every other store is recorded; then the tenant's schema,
identities, users and export history are gone, and its registry entry stays as a tombstone with the
record of what was removed. Each call runs as tenant_delete_user (SET ROLE), as the job does, which
holds no table privilege of its own.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one
up); skips when none is reachable.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

JOB = "tenant_delete_user"
GATEWAY = "api_gateway_user"


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
        return cur.fetchall() if cur.description else None
    finally:
        cur.execute("RESET ROLE")


@pytest.fixture(scope="module")
def db():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'complete_company_deletion'")
    if cur.fetchone() is None:
        pytest.skip("complete_company_deletion() not present — init scripts not applied")
    created = []
    yield cur, created
    for company_id in created:
        suffix = str(company_id).replace("-", "_")
        cur.execute("DELETE FROM public.company_deletion_steps WHERE company_id = %s", (company_id,))
        cur.execute("DELETE FROM public.company_deletions WHERE company_id = %s", (company_id,))
        cur.execute("DELETE FROM public.company_lifecycle_events WHERE company_id = %s", (company_id,))
        cur.execute(f'DROP SCHEMA IF EXISTS "tenant_{suffix}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        cur.execute("DELETE FROM public.companies WHERE company_id = %s", (company_id,))
    conn.close()


@pytest.fixture
def requested(db):
    """A tenant suspended past the wait, with a deletion requested — what the job finds."""
    cur, created = db
    [(company_id, *_)] = _as(cur, GATEWAY, "SELECT * FROM public.provision_company(%s)",
                             (f"delete-job-{uuid.uuid4().hex[:8]}",))
    company_id = str(company_id)
    created.append(company_id)
    _as(cur, GATEWAY, "SELECT * FROM public.update_company(%s, NULL, false)", (company_id,))
    cur.execute("UPDATE public.companies SET suspended_at = now() - interval '31 days' WHERE company_id = %s",
                (company_id,))
    _as(cur, GATEWAY, "SELECT * FROM public.request_company_deletion(%s, %s)", (company_id, str(uuid.uuid4())))
    return cur, company_id


def _required(cur):
    [(stores,)] = _as(cur, JOB, "SELECT public.company_deletion_required_stores()")
    return list(stores)


def _record_all(cur, company_id):
    for store in _required(cur):
        _as(cur, JOB, "SELECT public.record_company_deletion_step(%s, %s, NULL, '{}'::jsonb)", (company_id, store))


def test_the_job_finds_the_pending_deletion(requested):
    cur, company_id = requested
    pending = {str(c): removed for c, _, removed in _as(cur, JOB, "SELECT * FROM public.pending_company_deletions()")}
    assert pending[company_id] == []


def test_the_database_is_refused_while_a_store_is_pending(requested):
    cur, company_id = requested
    _as(cur, JOB, "SELECT public.record_company_deletion_step(%s, 'broker', now(), '{}'::jsonb)", (company_id,))
    with pytest.raises(errors.ObjectNotInPrerequisiteState) as e:
        _as(cur, JOB, "SELECT public.complete_company_deletion(%s, interval '14 days')", (company_id,))
    assert e.value.diag.message_hint == "stores_pending"
    cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (f"tenant_{company_id.replace('-', '_')}",))
    assert cur.fetchone() is not None


def test_a_store_is_recorded_once_and_only_a_known_store(requested):
    cur, company_id = requested
    first = _as(cur, JOB, "SELECT public.record_company_deletion_step(%s, 'broker', NULL, '{\"n\": 1}'::jsonb)",
                (company_id,))
    again = _as(cur, JOB, "SELECT public.record_company_deletion_step(%s, 'broker', NULL, '{\"n\": 2}'::jsonb)",
                (company_id,))
    assert first == [(True,)] and again == [(False,)]
    for store in ("database", "backups", "everything"):
        with pytest.raises(errors.InvalidParameterValue):
            _as(cur, JOB, "SELECT public.record_company_deletion_step(%s, %s, NULL, '{}'::jsonb)", (company_id, store))


def test_completion_empties_the_database_and_leaves_the_tombstone(requested):
    cur, company_id = requested
    suffix = company_id.replace("-", "_")
    _record_all(cur, company_id)
    [(detail,)] = _as(cur, JOB, "SELECT public.complete_company_deletion(%s, interval '14 days')", (company_id,))
    assert detail["schema"] == f"tenant_{suffix}"

    cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (f"tenant_{suffix}",))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM pg_roles WHERE rolname IN (%s, %s)",
                (f"tenant_reader_{suffix}", f"tenant_sqlproxy_{suffix}"))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT is_active, deleted_at IS NOT NULL FROM public.companies WHERE company_id = %s", (company_id,))
    assert cur.fetchone() == (False, True)  # the tombstone
    cur.execute("SELECT store, retained_until IS NOT NULL FROM public.company_deletion_steps "
                "WHERE company_id = %s AND store IN ('database', 'backups') ORDER BY store", (company_id,))
    assert cur.fetchall() == [("backups", True), ("database", False)]
    cur.execute("SELECT completed_at IS NOT NULL FROM public.company_deletions WHERE company_id = %s", (company_id,))
    assert cur.fetchone() == (True,)
    cur.execute("SELECT count(*) FROM public.company_lifecycle_events WHERE company_id = %s "
                "AND action = 'delete_completed'", (company_id,))
    assert cur.fetchone()[0] == 1
    assert company_id not in {str(c) for c, *_ in _as(cur, JOB, "SELECT * FROM public.pending_company_deletions()")}

    # Repeating the completion changes nothing, and a deleted tenant cannot be brought back.
    [(again,)] = _as(cur, JOB, "SELECT public.complete_company_deletion(%s, interval '14 days')", (company_id,))
    assert again == detail
    with pytest.raises(errors.ObjectNotInPrerequisiteState):
        _as(cur, GATEWAY, "SELECT * FROM public.update_company(%s, NULL, true)", (company_id,))


def test_the_job_role_reaches_no_table_directly(requested):
    cur, company_id = requested
    for statement in (
        "SELECT * FROM public.companies",
        "SELECT * FROM public.company_deletions",
        f'SELECT * FROM "tenant_{company_id.replace("-", "_")}".sensors',
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            _as(cur, JOB, statement)
    with pytest.raises(errors.InsufficientPrivilege):
        _as(cur, GATEWAY, "SELECT public.complete_company_deletion(%s, interval '14 days')", (company_id,))
