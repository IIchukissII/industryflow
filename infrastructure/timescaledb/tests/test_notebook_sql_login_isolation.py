# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Security proof for the notebook SQL proxy's upstream principal (ADR-0015 dec 5).

The proxy relays a kernel's Postgres protocol byte for byte, so a kernel can run anything the
upstream *session user* may run — ``SET ROLE``, ``RESET ROLE``, ``set_config('role', …)``,
``SET SESSION AUTHORIZATION``. Whatever that user can reach, the kernel can reach. This proves, at
the database and with no proxy in the way, that the session user the proxy opens a kernel's session
as (``tenant_sqlproxy_<uuid>``) reaches exactly its own tenant, whatever the kernel sends:

  * it is a member of its own tenant's reader and of the privilege-free pg_hba group — nothing else;
  * every way out (SET ROLE into another tenant's reader or the enrolment principal, RESET ROLE,
    set_config('role'), SET SESSION AUTHORIZATION, calling the enrolment function) is refused;
  * a cross-tenant SELECT is refused from every role it can reach;
  * the enrolment principal (``notebook_sql_proxy``) is a member of no tenant reader at all.

Tenants are provisioned through the real ``create_tenant_schema()``; the login is enrolled through
the real ``notebook_sql_proxy_enrol()`` as the enrolment principal, and then connected to over TCP
exactly as the proxy connects (TLS + SCRAM, the group-scoped pg_hba rule).

Needs a database with the init scripts applied and the notebooks profile's principals created
(NOTEBOOK_SQL_PROXY_DB_PASSWORD set). With no database it skips; with IF_REQUIRE_LIVE_STACK set (the
db-tenant-isolation workflow sets it) a skip is a failure, because a proof that did not run is not
a green result.
"""
import base64
import hashlib
import hmac
import os
import secrets
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402


def _skip(reason):
    if os.getenv("IF_REQUIRE_LIVE_STACK"):
        pytest.fail(f"required isolation proof could not run: {reason}")
    pytest.skip(reason)


def _dsn(**over):
    dsn = dict(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )
    dsn.update(over)
    return dsn


def _suffix(cid):
    return str(cid).replace("-", "_")


def _verifier(password: str) -> str:
    """The SCRAM-SHA-256 verifier Postgres stores — what the proxy enrols, never the password."""
    salt, iterations = secrets.token_bytes(16), 4096
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    stored = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    server = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored)}:{b64(server)}"


def _make_tenant(cur):
    cid = uuid.uuid4()
    cur.execute("SELECT create_tenant_schema(%s, %s)", (str(cid), f"test-{cid}"))
    schema = cur.fetchone()[0]
    cur.execute(
        f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
        "VALUES ('pump', 'unit-1', 1)"
    )
    return cid, schema


@pytest.fixture(scope="module")
def stack():
    try:
        admin = psycopg2.connect(**_dsn())
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        _skip(f"no database reachable: {e}")
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'create_tenant_schema'")
    if cur.fetchone() is None:
        _skip("create_tenant_schema() not present — init scripts not applied")
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'notebook_sql_proxy'")
    if cur.fetchone() is None:
        _skip("notebook_sql_proxy absent — the notebooks profile's principals were not created")

    a, b = _make_tenant(cur), _make_tenant(cur)
    password = secrets.token_urlsafe(32)
    # Enrol as the enrolment principal, so the EXECUTE grant is what is being exercised.
    cur.execute("SET ROLE notebook_sql_proxy")
    cur.execute("SELECT notebook_sql_proxy_enrol(%s, %s)", (str(a[0]), _verifier(password)))
    login = cur.fetchone()[0]
    cur.execute("RESET ROLE")

    kernel = psycopg2.connect(**_dsn(user=login, password=password))
    kernel.autocommit = True
    try:
        yield {"admin": cur, "kernel": kernel, "a": a, "b": b, "login": login}
    finally:
        kernel.close()
        for cid, schema in (a, b):
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{_suffix(cid)}"')
            cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{_suffix(cid)}"')
        admin.close()


def _refused(conn, sql, params=None):
    cur = conn.cursor()
    with pytest.raises(errors.InsufficientPrivilege):
        cur.execute(sql, params)
    cur.execute("RESET ROLE")


# --- the principal's shape ---------------------------------------------------------------------

def test_the_tenant_login_is_a_member_of_its_own_reader_and_nothing_else(stack):
    cid_a, _ = stack["a"]
    cur = stack["admin"]
    cur.execute(
        "SELECT g.rolname FROM pg_auth_members m "
        "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = %s ORDER BY 1",
        (stack["login"],),
    )
    assert [r[0] for r in cur.fetchall()] == sorted(
        ["notebook_sql_tenants", f"tenant_reader_{_suffix(cid_a)}"]
    )
    cur.execute(
        "SELECT rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
        "FROM pg_roles WHERE rolname = %s",
        (stack["login"],),
    )
    assert cur.fetchone() == (True, False, False, False, False, False)


def test_the_enrolment_principal_is_a_member_of_no_tenant(stack):
    cur = stack["admin"]
    cur.execute(
        "SELECT count(*) FROM pg_auth_members m JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = 'notebook_sql_proxy'"
    )
    assert cur.fetchone()[0] == 0


def test_the_hba_group_holds_no_privilege(stack):
    cur = stack["admin"]
    cur.execute(
        "SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = 'notebook_sql_tenants'"
    )
    assert cur.fetchone()[0] == 0


# --- what a kernel may do, it may do only within its tenant ------------------------------------

def test_the_login_reads_its_own_tenant_through_its_reader(stack):
    cid_a, schema_a = stack["a"]
    cur = stack["kernel"].cursor()
    cur.execute(f'SET ROLE "tenant_reader_{_suffix(cid_a)}"')
    cur.execute(f'SELECT count(*) FROM "{schema_a}".equipment')
    assert cur.fetchone()[0] == 1
    cur.execute("RESET ROLE")


def test_reset_role_lands_on_a_principal_that_reads_nothing(stack):
    _, schema_a = stack["a"]
    _refused(stack["kernel"], f'SELECT count(*) FROM "{schema_a}".equipment')


def test_set_role_into_another_tenants_reader_is_refused(stack):
    cid_b, _ = stack["b"]
    _refused(stack["kernel"], f'SET ROLE "tenant_reader_{_suffix(cid_b)}"')


def test_set_role_into_the_enrolment_principal_is_refused(stack):
    _refused(stack["kernel"], "SET ROLE notebook_sql_proxy")


def test_set_config_role_into_another_tenant_is_refused(stack):
    cid_b, _ = stack["b"]
    _refused(stack["kernel"], "SELECT set_config('role', %s, false)", (f"tenant_reader_{_suffix(cid_b)}",))


def test_set_session_authorization_is_refused(stack):
    cid_b, _ = stack["b"]
    _refused(stack["kernel"], f'SET SESSION AUTHORIZATION "tenant_sqlproxy_{_suffix(cid_b)}"')
    _refused(stack["kernel"], "SET SESSION AUTHORIZATION notebook_sql_proxy")


def test_cross_tenant_select_is_refused_from_every_reachable_role(stack):
    cid_a, _ = stack["a"]
    _, schema_b = stack["b"]
    conn = stack["kernel"]
    for role in (None, f"tenant_reader_{_suffix(cid_a)}", "notebook_sql_tenants"):
        cur = conn.cursor()
        cur.execute("RESET ROLE" if role is None else f'SET ROLE "{role}"')
        _refused(conn, f'SELECT count(*) FROM "{schema_b}".equipment')


def test_the_login_cannot_enrol_anything(stack):
    cid_b, _ = stack["b"]
    _refused(stack["kernel"], "SELECT notebook_sql_proxy_enrol(%s, %s)", (str(cid_b), _verifier("x")))


# --- the enrolment function -----------------------------------------------------------------------

def test_enrolment_refuses_a_plaintext_password(stack):
    cur = stack["admin"]
    with pytest.raises(errors.InvalidParameterValue):
        cur.execute("SELECT notebook_sql_proxy_enrol(%s, %s)", (str(stack["b"][0]), "a-plaintext-password"))


def test_enrolment_refuses_a_tenant_that_was_never_provisioned(stack):
    cur = stack["admin"]
    with pytest.raises(errors.UndefinedObject):
        cur.execute("SELECT notebook_sql_proxy_enrol(%s, %s)", (str(uuid.uuid4()), _verifier("x")))


def test_a_login_that_was_never_enrolled_cannot_authenticate(stack):
    cid_b, _ = stack["b"]
    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(**_dsn(user=f"tenant_sqlproxy_{_suffix(cid_b)}", password="anything"))
