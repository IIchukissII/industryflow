# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An alert rule always holds its anomaly threshold (script 37).

A rule stored with none before the script is given the column's default, the column then refuses
NULL, a second run changes nothing, and a tenant provisioned afterwards has the column required.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one up).
"""
import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

SCRIPT = Path(__file__).resolve().parents[1] / "init-scripts" / "37-rule-anomaly-threshold-required.sql"


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
    suffix = str(company_id).replace("-", "_")
    try:
        yield cur, schema
    finally:
        cur.execute("SET search_path TO public")
        # Each hypertable first: a compressed chunk lives outside the schema and outlives its drop.
        cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_schema = %s",
                    (schema,))
        for (name,) in cur.fetchall():
            cur.execute(f'DROP TABLE "{schema}"."{name}" CASCADE')
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        conn.close()


def _required(cur, schema):
    cur.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema = %s "
                "AND table_name = 'alert_rules' AND column_name = 'anomaly_threshold'", (schema,))
    return cur.fetchone()[0] == "NO"


def test_a_rule_without_a_threshold_gets_the_default_and_the_column_refuses_null(tenant):
    cur, schema = tenant
    table = f'"{schema}".alert_rules'
    # Provisioned now, the tenant already has the column required (create_tenant_schema calls 37).
    assert _required(cur, schema)
    # A tenant from before script 37, holding an ML rule stored with no threshold.
    cur.execute(f"ALTER TABLE {table} ALTER COLUMN anomaly_threshold DROP NOT NULL")
    cur.execute(f"INSERT INTO {table} (name, detection_type, model_id, anomaly_threshold) "
                "VALUES ('ml', 'ml', %s, NULL) RETURNING rule_id", (str(uuid.uuid4()),))
    rule = cur.fetchone()[0]
    script = SCRIPT.read_text(encoding="utf-8")
    for _ in range(2):
        cur.execute(script)
        cur.execute(f"SELECT anomaly_threshold FROM {table} WHERE rule_id = %s", (rule,))
        assert cur.fetchone()[0] == 0.85
        assert _required(cur, schema)
    with pytest.raises(psycopg2.errors.NotNullViolation):
        cur.execute(f"UPDATE {table} SET anomaly_threshold = NULL WHERE rule_id = %s", (rule,))
