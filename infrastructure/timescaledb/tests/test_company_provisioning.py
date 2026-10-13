# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Company provisioning through the gateway role (21-company-provisioning.sql).

api_gateway_user may not write the tenant registry or create schemas itself; it provisions a
tenant only through the SECURITY DEFINER provision_company()/update_company(), which validate
their input and build the schema with the real create_tenant_schema(). Each call here runs as
api_gateway_user (SET ROLE), exactly as the gateway does.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one
up); skips when none is reachable.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


@pytest.fixture(scope="module")
def db():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'provision_company'")
    if cur.fetchone() is None:
        pytest.skip("provision_company() not present — init scripts not applied")
    created = []
    yield cur, created
    for company_id in created:
        suffix = str(company_id).replace("-", "_")
        cur.execute(f'DROP SCHEMA IF EXISTS "tenant_{suffix}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        cur.execute("DELETE FROM public.companies WHERE company_id = %s", (str(company_id),))
    conn.close()


def _as(cur, role, sql, args=()):
    cur.execute(f'SET ROLE "{role}"')
    try:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else None
    finally:
        cur.execute("RESET ROLE")


def _provision(db, name):
    cur, created = db
    rows = _as(cur, "api_gateway_user", "SELECT * FROM public.provision_company(%s, %s)", (name, True))
    created.append(rows[0][0])
    return rows[0]


def test_gateway_cannot_write_the_registry_directly(db):
    cur, _ = db
    with pytest.raises(errors.InsufficientPrivilege):
        _as(cur, "api_gateway_user",
            "INSERT INTO public.companies (company_name, schema_name) VALUES (%s, %s)",
            (f"direct-{uuid.uuid4()}", "x"))


def test_provision_creates_the_row_and_the_tenant_schema(db):
    cur, _ = db
    name = f"  prov-{uuid.uuid4()}  "
    company_id, company_name, is_active, _created = _provision(db, name)
    assert company_name == name.strip() and is_active is True

    schema = "tenant_" + str(company_id).replace("-", "_")
    cur.execute("SELECT schema_name FROM public.companies WHERE company_id = %s", (str(company_id),))
    assert cur.fetchone()[0] == schema
    cur.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s "
        "AND table_name IN ('equipment', 'sensors', 'sensor_measurements', 'alert_rules')", (schema,))
    assert cur.fetchone()[0] == 4
    # The gateway can use the new tenant like any other.
    assert _as(cur, "api_gateway_user", f'SELECT count(*) FROM "{schema}".equipment') == [(0,)]


def _memberships(cur, role):
    cur.execute(
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
        "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = %s ORDER BY 1", (role,))
    return [r[0] for r in cur.fetchall()]


def test_provision_creates_the_sql_proxy_login_without_a_credential(db):
    # ADR-0033 dec 1 / ADR-0015 dec 5: the tenant's confined SQL login exists from provisioning on,
    # not from the proxy's first use, and holds no credential until the proxy enrols its verifier.
    cur, _ = db
    company_id, *_ = _provision(db, f"login-{uuid.uuid4()}")
    suffix = str(company_id).replace("-", "_")
    login = f"tenant_sqlproxy_{suffix}"
    cur.execute("SELECT rolcanlogin, rolinherit, rolpassword FROM pg_authid WHERE rolname = %s", (login,))
    assert cur.fetchone() == (True, False, None)
    assert _memberships(cur, login) == ["notebook_sql_tenants", f"tenant_reader_{suffix}"]


def test_reprovisioning_an_existing_tenant_converges_its_sql_login(db):
    # A tenant that predates eager creation (or lost its login) gets it back from provisioning.
    cur, _ = db
    company_id, company_name, *_ = _provision(db, f"heal-{uuid.uuid4()}")
    login = "tenant_sqlproxy_" + str(company_id).replace("-", "_")
    cur.execute(f'DROP ROLE "{login}"')
    cur.execute("SELECT create_tenant_schema(%s, %s)", (str(company_id), company_name))
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (login,))
    assert cur.fetchone() is not None


def test_duplicate_and_invalid_names_are_refused(db):
    cur, _ = db
    _company_id, name, *_ = _provision(db, f"dup-{uuid.uuid4()}")
    with pytest.raises(errors.UniqueViolation):
        _as(cur, "api_gateway_user", "SELECT * FROM public.provision_company(%s, true)", (name,))
    for bad in ("", "   ", "x" * 256, "line\nbreak"):
        with pytest.raises(errors.CheckViolation):
            _as(cur, "api_gateway_user", "SELECT * FROM public.provision_company(%s, true)", (bad,))


def test_update_renames_and_deactivates(db):
    cur, _ = db
    company_id, *_ = _provision(db, f"upd-{uuid.uuid4()}")
    new_name = f"renamed-{uuid.uuid4()}"
    rows = _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, %s, NULL)",
               (str(company_id), new_name))
    assert rows[0][1] == new_name and rows[0][2] is True
    rows = _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, NULL, false)",
               (str(company_id),))
    assert rows[0][1] == new_name and rows[0][2] is False
    assert _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, 'x', NULL)",
               (str(uuid.uuid4()),)) == []


def test_only_the_gateway_role_may_call(db):
    cur, _ = db
    with pytest.raises(errors.InsufficientPrivilege):
        _as(cur, "alert_service_user", "SELECT * FROM public.provision_company(%s, true)", (f"x-{uuid.uuid4()}",))


def test_a_function_planted_in_public_stops_provisioning(db):
    # api_gateway_user may create in public; an overload there would run with the definer's rights.
    cur, _ = db
    _as(cur, "api_gateway_user",
        "CREATE FUNCTION public.create_base_tables(p VARCHAR) RETURNS void LANGUAGE sql AS 'SELECT 1'")
    try:
        with pytest.raises(errors.InsufficientPrivilege, match="untrusted owner"):
            _as(cur, "api_gateway_user", "SELECT * FROM public.provision_company(%s, true)", (f"x-{uuid.uuid4()}",))
    finally:
        cur.execute("DROP FUNCTION public.create_base_tables(VARCHAR)")
