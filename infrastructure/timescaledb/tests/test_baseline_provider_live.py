# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The baseline provider's read on a real tenant, as the ML service's role (ADR-0044 dec 1, ADR-0023 dec 9).

Proves, for the statement as written in baseline_provider.py on asyncpg:

  * live, it reads the sensor's newest window;
  * as of an instant, the newest window at or before that window start, an older one;
  * a known sensor with no window yet answers its row with no value (unavailable);
  * an equipment without that sensor, or an equipment the tenant does not have, answers nothing
    (refused as unknown input).

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one up).
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")

import service_sql  # noqa: E402
from test_degraded_alerts import _connect  # noqa: E402

PROVIDER = "services/ml_service/api/feature_engineering/baseline_provider.py"
T0 = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


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
    cur.execute(f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
                "VALUES ('pump', 'p1', 2) RETURNING equipment_id")
    equipment = cur.fetchone()[0]
    sensors = {}
    for position, name in enumerate(("pressure", "flow")):
        cur.execute(f'INSERT INTO "{schema}".sensors (equipment_id, sensor_name, sensor_type, position) '
                    "VALUES (%s, %s, 'analog', %s) RETURNING sensor_id", (equipment, name, position))
        sensors[name] = cur.fetchone()[0]
    for minutes, mean in ((0, 10.0), (5, 20.0), (10, 30.0)):
        cur.execute(f'INSERT INTO "{schema}".sensor_aggregations_1min '
                    "(time, sensor_id, equipment_id, avg_value, stddev_value, count_values) "
                    "VALUES (%s, %s, %s, %s, 1.0, 60)",
                    (T0 + timedelta(minutes=minutes), sensors["pressure"], equipment, mean))
    try:
        yield schema, uuid.UUID(str(equipment))
    finally:
        cur.execute("SET search_path TO public")
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        conn.close()


def _read(schema, equipment, sensor, bound):
    template = service_sql.literal(PROVIDER, "BASELINE_SELECT")
    stmt = service_sql.statement(PROVIDER, "BASELINE_SELECT", template.format(table="sensor_aggregations_1min"))
    return service_sql.run(stmt, (str(equipment), sensor, bound), role="ml_service_user",
                           search_path=f'"{schema}", public')


def test_live_reads_the_newest_window(tenant):
    [row] = _read(*tenant, "pressure", None)
    assert row["avg_value"] == 30.0


def test_as_of_reads_the_newest_window_at_or_before_the_bound(tenant):
    [row] = _read(*tenant, "pressure", T0 + timedelta(minutes=7))
    assert row["avg_value"] == 20.0
    [row] = _read(*tenant, "pressure", T0 + timedelta(minutes=5))
    assert row["avg_value"] == 20.0


def test_a_known_sensor_with_no_window_answers_its_row_with_no_value(tenant):
    [row] = _read(*tenant, "flow", None)
    assert row["sensor_id"] is not None and row["avg_value"] is None
    [row] = _read(*tenant, "pressure", T0 - timedelta(minutes=1))
    assert row["avg_value"] is None, "no window was written before the first"


def test_an_unknown_sensor_or_equipment_answers_nothing(tenant):
    schema, equipment = tenant
    assert _read(schema, equipment, "temperature", None) == []
    assert _read(schema, uuid.uuid4(), "pressure", None) == []
