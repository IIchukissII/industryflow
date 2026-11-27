# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The gateway's latest-reading cache lists a tenant only while the registry holds it active (ADR-0003
dec 1, ADR-0033 dec 3).

The cache updater queries each listed tenant's schema; a tenant being deleted has its schema dropped
under it. This runs the updater's own statement as api_gateway_user on the gateway's driver against a
real tenant: active, suspended, and with a deletion requested.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one
up); skips when none is reachable.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
tenancy = pytest.importorskip("industryflow_contracts.tenancy")
from psycopg2 import errors  # noqa: E402

import service_sql  # noqa: E402
from industryflow_contracts import testing  # noqa: E402

GATEWAY = "api_gateway_user"
ACTIVE_TENANTS = service_sql.statement("services/api_gateway/messaging/cache_updater.py",
                                       "ACTIVE_TENANTS_SELECT")


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


def _as(cur, sql, args=()):
    cur.execute(f'SET ROLE "{GATEWAY}"')
    try:
        cur.execute(sql, args)
        return cur.fetchall()
    finally:
        cur.execute("RESET ROLE")


@pytest.fixture
def tenant():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'request_company_deletion'")
    if cur.fetchone() is None:
        pytest.skip("request_company_deletion() not present — init scripts not applied")
    [(company_id, *_)] = _as(cur, "SELECT * FROM public.provision_company(%s)",
                             (f"cache-tenants-{uuid.uuid4().hex[:8]}",))
    company_id = str(company_id)
    yield cur, company_id
    cur.execute("DELETE FROM public.company_deletions WHERE company_id = %s", (company_id,))
    cur.execute("DELETE FROM public.company_lifecycle_events WHERE company_id = %s", (company_id,))
    cur.execute(testing.drop_tenant_schema_sql(f'{tenancy.schema_name(company_id)}'))
    cur.execute(f'DROP ROLE IF EXISTS "{tenancy.sql_login_name(company_id)}"')
    cur.execute(f'DROP ROLE IF EXISTS "{tenancy.reader_role_name(company_id)}"')
    cur.execute("DELETE FROM public.companies WHERE company_id = %s", (company_id,))
    conn.close()


def _listed():
    return {str(row["company_id"]) for row in service_sql.run(ACTIVE_TENANTS, role=GATEWAY)}


def _set_active(cur, company_id, active):
    _as(cur, "SELECT * FROM public.update_company(%s, NULL, %s)", (company_id, active))


def test_an_active_tenant_is_listed_and_a_suspended_one_is_not(tenant):
    cur, company_id = tenant
    assert company_id in _listed()
    _set_active(cur, company_id, False)
    assert company_id not in _listed()


def test_a_tenant_with_a_deletion_request_is_never_listed_again(tenant):
    """The request cannot be undone, so the tenant cannot return to the list while the delete job
    drops its schema."""
    cur, company_id = tenant
    assert company_id in _listed()
    _set_active(cur, company_id, False)
    # As the superuser: only the clock is moved, the way 30 days of waiting would.
    cur.execute("UPDATE public.companies SET suspended_at = now() - interval '31 days' "
                "WHERE company_id = %s", (company_id,))
    _as(cur, "SELECT * FROM public.request_company_deletion(%s, %s)", (company_id, str(uuid.uuid4())))
    with pytest.raises(errors.ObjectNotInPrerequisiteState):
        _set_active(cur, company_id, True)
    # What the delete job does to the database store.
    cur.execute(f'DROP SCHEMA "{tenancy.schema_name(company_id)}" CASCADE')
    assert company_id not in _listed()
