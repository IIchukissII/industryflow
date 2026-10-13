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


# --- Suspension (ADR-0033 dec 3): the tenant's database identity follows its active flag ---------

_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "init-scripts")


def _function_body(script, signature):
    sql = open(os.path.join(_SCRIPTS, script)).read()
    start = sql.index(f"CREATE OR REPLACE FUNCTION {signature}")
    return sql[start:sql.index("$$;", start)]


def test_update_company_reasserts_the_sql_login_when_the_flag_changes():
    body = _function_body("21-company-provisioning.sql", "public.update_company(")
    assert "PERFORM public.ensure_tenant_sql_login(p_company_id)" in body, (
        "deactivating a company leaves its SQL login able to log in")


def test_the_sql_login_is_nologin_while_suspended():
    body = _function_body("13-notebook-sql-proxy-role.sh", "ensure_tenant_sql_login(")
    assert "tenant_is_suspended(p_company_id)" in body
    assert "THEN 'NOLOGIN' ELSE 'LOGIN'" in body, "the login's LOGIN attribute ignores suspension"
    assert "'CREATE ROLE %I LOGIN" not in body and "'ALTER ROLE %I LOGIN" not in body


def test_open_sessions_are_ended_only_after_the_suspension_commits():
    # NOLOGIN takes effect at commit: a session ended inside the suspending transaction could
    # reconnect before it. So neither function that runs in that transaction terminates anything.
    for script, fn in (("13-notebook-sql-proxy-role.sh", "ensure_tenant_sql_login("),
                       ("21-company-provisioning.sql", "public.update_company(")):
        assert "pg_terminate_backend" not in _function_body(script, fn), f"{fn} terminates in-transaction"
    body = _function_body("21-company-provisioning.sql", "public.end_suspended_tenant_sql_sessions(")
    assert "pg_terminate_backend" in body
    assert "NOT c.is_active" in body and "NOT rolcanlogin" in body, (
        "the terminator must act only on a committed suspension")
    sql = open(os.path.join(_SCRIPTS, "21-company-provisioning.sql")).read()
    assert "GRANT EXECUTE ON FUNCTION public.end_suspended_tenant_sql_sessions(UUID) TO api_gateway_user;" in sql


def test_the_proxy_does_not_enrol_a_suspended_tenant():
    body = _function_body("13-notebook-sql-proxy-role.sh", "notebook_sql_proxy_enrol(")
    assert body.index("tenant_is_suspended(p_company_id)") < body.index("ensure_tenant_sql_login"), (
        "enrolment must refuse a suspended tenant before it touches the login")


def _can_login(cur, role):
    cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (role,))
    return cur.fetchone()[0]


def test_deactivation_disables_the_sql_login_and_reactivation_restores_it(db):
    cur, _ = db
    company_id, *_ = _provision(db, f"suspend-{uuid.uuid4()}")
    login = "tenant_sqlproxy_" + str(company_id).replace("-", "_")
    assert _can_login(cur, login) is True
    _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, NULL, false)", (str(company_id),))
    assert _can_login(cur, login) is False
    # Re-asserting the login (backfill, enrolment's helper) keeps a suspended tenant NOLOGIN.
    cur.execute("SELECT ensure_tenant_sql_login(%s)", (str(company_id),))
    assert _can_login(cur, login) is False
    _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, NULL, true)", (str(company_id),))
    assert _can_login(cur, login) is True


def _login_connection(cur, login):
    # The proxy's verifier is not available here, so the test gives the login a password itself.
    password = f"pw-{uuid.uuid4()}"
    cur.execute(f'ALTER ROLE "{login}" PASSWORD %s', (password,))
    conn = psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=login, password=password, connect_timeout=5)
    conn.autocommit = True
    return conn, password


def test_suspension_ends_open_sessions_after_commit_and_none_can_start(db):
    cur, _ = db
    company_id, *_ = _provision(db, f"suspend-race-{uuid.uuid4()}")
    login = "tenant_sqlproxy_" + str(company_id).replace("-", "_")
    try:
        conn, password = _login_connection(cur, login)
    except psycopg2.OperationalError as e:
        pytest.skip(f"the tenant login cannot connect here: {e}")

    # The gateway's sequence: suspend and commit, then end the sessions in a transaction of its own.
    _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, NULL, false)", (str(company_id),))
    conn.cursor().execute("SELECT 1")  # still open: nothing is ended inside the suspension
    rows = _as(cur, "api_gateway_user", "SELECT public.end_suspended_tenant_sql_sessions(%s)",
               (str(company_id),))
    assert rows[0][0] >= 1
    with pytest.raises(psycopg2.Error):
        conn.cursor().execute("SELECT 1")
    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
                         dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
                         user=login, password=password, connect_timeout=5)


def test_the_terminator_never_touches_an_active_tenant(db):
    cur, _ = db
    company_id, *_ = _provision(db, f"active-{uuid.uuid4()}")
    rows = _as(cur, "api_gateway_user", "SELECT public.end_suspended_tenant_sql_sessions(%s)",
               (str(company_id),))
    assert rows[0][0] == 0


def test_enrolment_refuses_a_suspended_tenant(db):
    cur, _ = db
    company_id, *_ = _provision(db, f"suspend-enrol-{uuid.uuid4()}")
    _as(cur, "api_gateway_user", "SELECT * FROM public.update_company(%s, NULL, false)", (str(company_id),))
    verifier = "SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVk:c2VydmVy"
    with pytest.raises(errors.InsufficientPrivilege, match="suspended"):
        cur.execute("SELECT notebook_sql_proxy_enrol(%s, %s)", (str(company_id), verifier))
