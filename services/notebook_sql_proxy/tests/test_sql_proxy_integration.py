# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""End-to-end proof for the SQL access proxy (ADR-0015 dec 5) — the open work in issue #19.

Every other proxy test uses a fake backend/store; this one drives the REAL relay for the first
time. It stands up nothing itself — it expects a live notebooks-profile stack reachable via env
(TimescaleDB with the init scripts + the ``notebook_sql_proxy`` privileged role, Redis, and the
proxy on :6432) — and proves the three properties the fake tests cannot:

  * a valid SQL-audience handle reads its OWN tenant, read-only, through the proxy;
  * that same handle CANNOT read another tenant (cross-tenant denied at the GRANT boundary,
    exactly as the reader-role isolation test proves at the DB level — here through the proxy);
  * a revoked handle is refused (no backend opened);
  * an API-audience handle is refused at the SQL proxy (planes are not interchangeable).

It drives the proxy as a kernel does: a Postgres client presenting the capability handle in
place of the password (ADR-0015 dec 5). Kernel→proxy is plaintext cleartext-password (the proxy
declines SSLRequest and issues AuthenticationCleartextPassword); the proxy holds the only real
DB credential and speaks TLS+SCRAM upstream on the kernel's behalf.

When the stack is not reachable it SKIPS — so it never fails a unit-only run. Run it against a
live stack (see the service README): bring up the ``notebooks`` compose profile, then
``PROXY_HOST=localhost pytest tests/test_sql_proxy_integration.py``.
"""
import os
import sys
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
redis = pytest.importorskip("redis")
from psycopg2 import errors  # noqa: E402

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import binding as b  # noqa: E402  — reuse the proxy's own key/role derivation, don't reinvent


# --------------------------------------------------------------------------- connection helpers

def _admin_dsn():
    """Superuser connection used only to provision/seed/tear down tenants (not through the proxy)."""
    return dict(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


def _redis_client():
    return redis.Redis.from_url(os.getenv("SQL_PROXY_REDIS_URL", "redis://localhost:6379/0"))


def _proxy_connect(handle: str):
    """Connect THROUGH the proxy, presenting the capability handle as the password (ADR-0015)."""
    return psycopg2.connect(
        host=os.getenv("PROXY_HOST", "localhost"),
        port=int(os.getenv("PROXY_PORT", "6432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PROXY_CLIENT_USER", "notebook"),  # ignored by the proxy; the handle is auth
        password=handle,
        sslmode="disable",  # kernel→proxy hop is plaintext cleartext-password; the proxy does TLS upstream
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


def _mint(rc, *, company_id, audience="sql", user="e2e-tester"):
    """Mint a capability directly into the store, mirroring notebook_hub.capabilities.mint."""
    import json
    handle = "e2e-" + uuid.uuid4().hex
    record = {"user": user, "company_id": company_id, "audience": audience, "read_only": True}
    rc.set(f"{b._KEY_PREFIX}{handle}", json.dumps(record), ex=300)
    return handle


# ------------------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def stack():
    """Two provisioned tenants + a Redis handle, or a skip if the live stack isn't reachable."""
    try:
        conn = psycopg2.connect(**_admin_dsn())
    except Exception as e:  # noqa: BLE001 - any failure means "no live DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'create_tenant_schema'")
    if cur.fetchone() is None:
        pytest.skip("create_tenant_schema() not present — init scripts not applied")
    # The proxy's privileged role only exists on the notebooks profile; without it a tenant's
    # reader grants no membership to the proxy and the relay could not SET ROLE — so this test
    # is meaningless. Skip rather than assert a false negative.
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'notebook_sql_proxy'")
    if cur.fetchone() is None:
        pytest.skip("notebook_sql_proxy role absent — notebooks profile not up")

    try:
        rc = _redis_client()
        rc.ping()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no redis reachable: {e}")

    def make_tenant():
        cid = uuid.uuid4()
        cur.execute("SELECT create_tenant_schema(%s, %s)", (str(cid), f"e2e-{cid}"))
        schema = cur.fetchone()[0]
        cur.execute(
            f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
            "VALUES ('pump', 'unit-1', 1)"
        )
        return cid, schema

    a = make_tenant()
    b_tenant = make_tenant()

    # Fail fast (skip) if the proxy port isn't even open — a valid handle must connect.
    good = _mint(rc, company_id=str(a[0]))
    try:
        probe = _proxy_connect(good)
        probe.close()
    except Exception as e:  # noqa: BLE001
        for _cid, schema in (a, b_tenant):
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'DROP ROLE IF EXISTS "{b.reader_role(str(_cid))}"')
        conn.close()
        pytest.skip(f"proxy not reachable on {os.getenv('PROXY_HOST', 'localhost')}:"
                    f"{os.getenv('PROXY_PORT', '6432')}: {e}")

    try:
        yield {"cur": cur, "rc": rc, "a": a, "b": b_tenant}
    finally:
        for _cid, schema in (a, b_tenant):
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'DROP ROLE IF EXISTS "{b.reader_role(str(_cid))}"')
        conn.close()


# ---------------------------------------------------------------------------------------- proofs

def test_valid_handle_reads_own_tenant(stack):
    cid_a, schema_a = stack["a"]
    handle = _mint(stack["rc"], company_id=str(cid_a))
    conn = _proxy_connect(handle)
    try:
        cur = conn.cursor()
        # Unqualified: resolves through the search_path the proxy set to this tenant's schema.
        cur.execute("SELECT count(*) FROM equipment")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_valid_handle_is_read_only(stack):
    cid_a, schema_a = stack["a"]
    handle = _mint(stack["rc"], company_id=str(cid_a))
    conn = _proxy_connect(handle)
    try:
        cur = conn.cursor()
        # The proxy enforces read-only two ways (belt and suspenders): it SETs the session
        # default_transaction_read_only = on AND assumes a reader role with no write GRANT. So a
        # write is refused as either a ReadOnlySqlTransaction (the transaction guard trips first)
        # or InsufficientPrivilege (the GRANT) — accept both; the point is it is refused.
        with pytest.raises((errors.ReadOnlySqlTransaction, errors.InsufficientPrivilege)):
            cur.execute(
                "INSERT INTO equipment (equipment_type, name, sensor_count) "
                "VALUES ('valve', 'unit-2', 1)"
            )
    finally:
        conn.close()


def test_handle_cannot_read_another_tenant(stack):
    cid_a, _ = stack["a"]
    _, schema_b = stack["b"]
    handle = _mint(stack["rc"], company_id=str(cid_a))
    conn = _proxy_connect(handle)
    try:
        cur = conn.cursor()
        # Cross-tenant read is denied by the GRANT boundary, not search_path discipline.
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(f'SELECT count(*) FROM "{schema_b}".equipment')
    finally:
        conn.close()


def test_revoked_handle_is_refused(stack):
    cid_a, _ = stack["a"]
    handle = _mint(stack["rc"], company_id=str(cid_a))
    stack["rc"].delete(f"{b._KEY_PREFIX}{handle}")  # revoke = delete the store entry (ADR-0015 dec 2)
    with pytest.raises(psycopg2.OperationalError):
        _proxy_connect(handle)


def test_api_audience_handle_is_refused(stack):
    cid_a, _ = stack["a"]
    handle = _mint(stack["rc"], company_id=str(cid_a), audience="api")  # wrong plane
    with pytest.raises(psycopg2.OperationalError):
        _proxy_connect(handle)
