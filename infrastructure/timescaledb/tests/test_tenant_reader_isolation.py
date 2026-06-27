# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Security proof for the per-tenant read-only role (ADR-0011 dec 3, ADR-0012 dec 4).

This is the keystone test of the notebook data boundary: it provisions two tenants through
the real ``create_tenant_schema()`` function and proves, by assuming the per-tenant reader
role exactly as the notebook SQL proxy will (``SET ROLE``), that the boundary is enforced by
GRANT — not by ``search_path`` discipline:

  * the reader of tenant A can read tenant A,
  * the reader of tenant A CANNOT read tenant B (cross-tenant denied),
  * the reader of tenant A CANNOT write anywhere (read-only).

It requires a running database with the init scripts applied (the CI workflow stands one up
via docker compose). When no database is reachable it skips, so it never fails a unit-only run.
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


def _reader_role(company_id: uuid.UUID) -> str:
    return "tenant_reader_" + str(company_id).replace("-", "_")


def _make_tenant(cur):
    """Provision a tenant via the real function and seed one equipment row as superuser."""
    company_id = uuid.uuid4()
    cur.execute("SELECT create_tenant_schema(%s, %s)", (str(company_id), f"test-{company_id}"))
    schema = cur.fetchone()[0]
    cur.execute(
        f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
        "VALUES ('pump', 'unit-1', 1)"
    )
    return company_id, schema


@pytest.fixture(scope="module")
def tenants():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'create_tenant_schema'")
    if cur.fetchone() is None:
        pytest.skip("create_tenant_schema() not present — init scripts not applied")

    a = _make_tenant(cur)
    b = _make_tenant(cur)
    try:
        yield {"cur": cur, "a": a, "b": b}
    finally:
        for _cid, schema in (a, b):
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'DROP ROLE IF EXISTS "{_reader_role(_cid)}"')
        conn.close()


def test_reader_can_read_its_own_tenant(tenants):
    cur, (cid_a, schema_a) = tenants["cur"], tenants["a"]
    cur.execute(f'SET ROLE "{_reader_role(cid_a)}"')
    try:
        cur.execute(f'SELECT count(*) FROM "{schema_a}".equipment')
        assert cur.fetchone()[0] == 1
    finally:
        cur.execute("RESET ROLE")


def test_reader_cannot_read_another_tenant(tenants):
    cur, (cid_a, _), (_, schema_b) = tenants["cur"], tenants["a"], tenants["b"]
    cur.execute(f'SET ROLE "{_reader_role(cid_a)}"')
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(f'SELECT count(*) FROM "{schema_b}".equipment')
    finally:
        cur.execute("RESET ROLE")


def test_reader_cannot_write_its_own_tenant(tenants):
    cur, (cid_a, schema_a) = tenants["cur"], tenants["a"]
    cur.execute(f'SET ROLE "{_reader_role(cid_a)}"')
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(
                f'INSERT INTO "{schema_a}".equipment (equipment_type, name, sensor_count) '
                "VALUES ('valve', 'unit-2', 1)"
            )
    finally:
        cur.execute("RESET ROLE")


def test_reader_role_is_nologin(tenants):
    """The reader is assumed via SET ROLE by the proxy; it must not be directly connectable."""
    cur, (cid_a, _) = tenants["cur"], tenants["a"]
    cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (_reader_role(cid_a),))
    row = cur.fetchone()
    assert row is not None and row[0] is False
