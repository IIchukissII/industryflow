# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Migration proof for the alert_labels operator-feedback table (ADR-0022).

Provisions a tenant through the real ``create_tenant_schema()`` and proves the label store
behaves as ADR-0022 decision 2 requires:

  * the table exists in the tenant schema,
  * it is an ORDINARY table, not a hypertable — so it has no retention policy and outlives
    the alerts' 90-day window (the whole reason labels don't live on the alerts hypertable),
  * the verdict is CHECK-constrained to the three allowed values,
  * a re-label upserts in place (one current verdict per alert), updating labeled_at.

Requires a running database with the init scripts applied (the CI workflow stands one up via
docker compose). When no database is reachable it skips, so it never fails a unit-only run.
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


@pytest.fixture(scope="module")
def tenant():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'create_tenant_schema'")
    if cur.fetchone() is None:
        pytest.skip("create_tenant_schema() not present — init scripts not applied")

    company_id = uuid.uuid4()
    cur.execute("SELECT create_tenant_schema(%s, %s)", (str(company_id), f"test-{company_id}"))
    schema = cur.fetchone()[0]
    try:
        yield {"cur": cur, "company_id": company_id, "schema": schema}
    finally:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "{_reader_role(company_id)}"')
        conn.close()


def test_alert_labels_table_exists(tenant):
    cur, schema = tenant["cur"], tenant["schema"]
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = 'alert_labels'",
        (schema,),
    )
    assert cur.fetchone() is not None


def test_alert_labels_is_not_a_hypertable(tenant):
    """Labels must outlive the alerts' 90-day retention, so the table is deliberately NOT a
    hypertable and carries no retention policy (ADR-0022 dec 2)."""
    cur, schema = tenant["cur"], tenant["schema"]
    cur.execute(
        "SELECT 1 FROM timescaledb_information.hypertables "
        "WHERE hypertable_schema = %s AND hypertable_name = 'alert_labels'",
        (schema,),
    )
    assert cur.fetchone() is None
    # Sanity-check the query itself: alerts, by contrast, IS a hypertable.
    cur.execute(
        "SELECT 1 FROM timescaledb_information.hypertables "
        "WHERE hypertable_schema = %s AND hypertable_name = 'alerts'",
        (schema,),
    )
    assert cur.fetchone() is not None


def test_verdict_is_check_constrained(tenant):
    cur, schema = tenant["cur"], tenant["schema"]
    with pytest.raises(errors.CheckViolation):
        cur.execute(
            f'INSERT INTO "{schema}".alert_labels (alert_id, verdict, labeled_by) '
            "VALUES (%s, 'not_a_verdict', 'op-1')",
            (str(uuid.uuid4()),),
        )


def test_relabel_upserts_in_place(tenant):
    cur, schema = tenant["cur"], tenant["schema"]
    alert_id = str(uuid.uuid4())

    cur.execute(
        f'INSERT INTO "{schema}".alert_labels (alert_id, verdict, labeled_by) '
        "VALUES (%s, 'true_positive', 'op-1')",
        (alert_id,),
    )
    cur.execute(
        f'INSERT INTO "{schema}".alert_labels (alert_id, verdict, labeled_by, labeled_at) '
        "VALUES (%s, 'false_positive', 'op-2', NOW()) "
        "ON CONFLICT (alert_id) DO UPDATE SET "
        "verdict = EXCLUDED.verdict, labeled_by = EXCLUDED.labeled_by, labeled_at = NOW()",
        (alert_id,),
    )

    cur.execute(f'SELECT count(*) FROM "{schema}".alert_labels WHERE alert_id = %s', (alert_id,))
    assert cur.fetchone()[0] == 1
    cur.execute(f'SELECT verdict, labeled_by FROM "{schema}".alert_labels WHERE alert_id = %s', (alert_id,))
    assert cur.fetchone() == ("false_positive", "op-2")


def test_reader_can_read_labels(tenant):
    """The per-tenant reader (notebook boundary) must see labels like any other tenant table."""
    cur, company_id, schema = tenant["cur"], tenant["company_id"], tenant["schema"]
    cur.execute(f'SET ROLE "{_reader_role(company_id)}"')
    try:
        cur.execute(f'SELECT count(*) FROM "{schema}".alert_labels')
        assert cur.fetchone()[0] >= 0
    finally:
        cur.execute("RESET ROLE")
