# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A window of one reading already written gets zero spread, compressed chunks included
(ADR-0006 dec 9; script 36).

The script runs as written over a real tenant's aggregate tables: a one-reading window with no spread
gets zero, in a compressed chunk as in an open one; a stored spread and a missing one on a window of
more readings are left as they are; a second run changes nothing.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one up).
"""
import os
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

SCRIPT = Path(__file__).resolve().parents[1] / "init-scripts" / "36-window-spread-of-one-reading.sql"


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
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        conn.close()


def test_a_one_reading_window_gets_zero_spread_compressed_or_not(tenant):
    cur, schema = tenant
    table = f'"{schema}".sensor_aggregations_1min'
    equipment = str(uuid.uuid4())
    rows = {  # name: (age, stddev_value, count_values)
        "old_single": ("10 days", None, 1),
        "single": ("1 hour", None, 1),
        "spread": ("2 hours", 1.5, 3),
        "missing": ("3 hours", None, 4),
    }
    sensors = {name: str(uuid.uuid4()) for name in rows}
    for name, (age, std, n) in rows.items():
        cur.execute(f"INSERT INTO {table} (time, sensor_id, equipment_id, avg_value, stddev_value, count_values) "
                    f"VALUES (date_trunc('minute', now() - interval '{age}'), %s, %s, 1.0, %s, %s)",
                    (sensors[name], equipment, std, n))
    # The old window's chunk compressed, as the policy leaves windows older than its horizon.
    cur.execute(f"SELECT compress_chunk(c) FROM show_chunks('{table}', older_than => now() - interval '5 days') c")
    assert cur.fetchall(), "no chunk was compressed: the compressed case is not exercised"

    def spreads():
        cur.execute(f"SELECT sensor_id::text, stddev_value FROM {table} WHERE equipment_id = %s", (equipment,))
        by_sensor = dict(cur.fetchall())
        return {name: by_sensor[sensors[name]] for name in rows}

    script = SCRIPT.read_text(encoding="utf-8")
    cur.execute(script)
    assert spreads() == {"old_single": 0.0, "single": 0.0, "spread": 1.5, "missing": None}
    cur.execute(script)
    assert spreads() == {"old_single": 0.0, "single": 0.0, "spread": 1.5, "missing": None}
