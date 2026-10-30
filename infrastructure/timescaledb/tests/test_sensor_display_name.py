# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Migration proof for the sensor display name (init script 22), and the gateway's name joins.

Provisions a tenant through the real ``create_tenant_schema()`` and proves:

  * the tenant's sensors table has a nullable ``display_name`` VARCHAR(100), and the key
    ``sensor_name`` is unchanged,
  * the migration function is idempotent (re-applying it to a migrated schema is a no-op),
  * the api_gateway's alert-rule list and alert-history SELECTs, read from the gateway's source
    and run on its driver as its role inside the tenant schema (service_sql.py), return the names of what a rule or alert
    points at, and NULL names (not a missing row) for a target that was deleted.

The SELECTs are read from the source because the failure they guard is a query that succeeds and
returns less. Requires a running database with the init scripts applied (the CI workflow stands
one up via docker compose). When no database is reachable it skips.
"""
import dataclasses
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

import service_sql  # noqa: E402

GATEWAY_SOURCES = {
    "RULE_LIST_SELECT": "services/api_gateway/routers/alert_rules.py",
    "_ALERT_SELECT": "services/api_gateway/routers/alerts_history.py",
}


def _gateway_rows(cur, name, order_by):
    """The gateway's SELECT, on its driver, as its role, in the tenant's path, ordered for the test."""
    stmt = service_sql.statement(GATEWAY_SOURCES[name], name)
    stmt = dataclasses.replace(stmt, sql=f"{stmt.sql} ORDER BY {order_by}")
    cur.execute("SHOW search_path")
    return service_sql.run(stmt, {}, role="api_gateway_user", search_path=cur.fetchone()[0])


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
        yield {"cur": cur, "schema": schema}
    finally:
        cur.execute("SET search_path TO public")
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        conn.close()


def _column(cur, schema, name):
    cur.execute(
        "SELECT data_type, character_maximum_length, is_nullable FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = 'sensors' AND column_name = %s",
        (schema, name),
    )
    return cur.fetchone()


def test_a_new_tenant_has_the_display_name_column(tenant):
    cur, schema = tenant["cur"], tenant["schema"]
    assert _column(cur, schema, "display_name") == ("character varying", 100, "YES")
    assert _column(cur, schema, "sensor_name") == ("character varying", 100, "NO")


def test_the_migration_is_idempotent(tenant):
    cur, schema = tenant["cur"], tenant["schema"]
    cur.execute("SELECT add_display_name_column_to_sensors(%s)", (schema,))
    cur.execute("SELECT add_display_name_column_to_sensors(%s)", (schema,))
    assert _column(cur, schema, "display_name") == ("character varying", 100, "YES")


@pytest.fixture(scope="module")
def targets(tenant):
    cur, schema = tenant["cur"], tenant["schema"]
    cur.execute(f'SET search_path TO "{schema}", public')
    cur.execute("INSERT INTO equipment (equipment_type, name, sensor_count) VALUES ('pump', 'Pump A', 2) "
                "RETURNING equipment_id")
    eq = cur.fetchone()[0]
    cur.execute("INSERT INTO sensors (equipment_id, sensor_name, display_name, sensor_type, position) "
                "VALUES (%s, 'temp_root_1', 'Root zone', 'temperature', 0) RETURNING sensor_id", (eq,))
    labelled = cur.fetchone()[0]
    cur.execute("INSERT INTO sensors (equipment_id, sensor_name, sensor_type, position) "
                "VALUES (%s, 'co2', 'gas', 1) RETURNING sensor_id", (eq,))
    keyed = cur.fetchone()[0]
    cur.execute("INSERT INTO ml_models (model_name, model_type, model_version) "
                "VALUES ('pump-iforest', 'isolation_forest', 3) RETURNING model_id")
    model = cur.fetchone()[0]
    return {"cur": cur, "eq": eq, "labelled": labelled, "keyed": keyed, "model": model,
            "gone": str(uuid.uuid4())}


def test_the_rule_list_names_its_targets_and_survives_a_deleted_one(targets):
    t = targets
    cur = t["cur"]
    cur.execute("""
        INSERT INTO alert_rules (name, sensor_id, equipment_id, detection_type, model_id, severity) VALUES
            ('labelled', %(labelled)s, NULL, 'threshold', NULL, 'low'),
            ('keyed', %(keyed)s, NULL, 'threshold', NULL, 'low'),
            ('dangling', %(gone)s, NULL, 'threshold', NULL, 'low'),
            ('machine', NULL, %(eq)s, 'ml', %(model)s, 'high'),
            ('lost model', NULL, NULL, 'ml', %(gone)s, 'high')
    """, t)
    rows = {r["name"]: r for r in _gateway_rows(cur, "RULE_LIST_SELECT", "r.name")}

    assert len(rows) == 5  # a LEFT JOIN: a rule whose target is gone is still listed
    assert (rows["labelled"]["sensor_name"], rows["labelled"]["sensor_display_name"]) == ("temp_root_1", "Root zone")
    assert (rows["keyed"]["sensor_name"], rows["keyed"]["sensor_display_name"]) == ("co2", None)
    assert rows["dangling"]["sensor_name"] is None and str(rows["dangling"]["sensor_id"]) == t["gone"]
    assert rows["machine"]["equipment_name"] == "Pump A"
    assert (rows["machine"]["model_name"], rows["machine"]["model_version"]) == ("pump-iforest", 3)
    assert rows["lost model"]["model_name"] is None and str(rows["lost model"]["model_id"]) == t["gone"]


def test_alert_history_carries_the_sensor_label_and_rule_name(targets):
    t = targets
    cur = t["cur"]
    cur.execute("INSERT INTO alert_rules (name, severity) VALUES ('history rule', 'low') RETURNING rule_id")
    rule = cur.fetchone()[0]
    cur.execute("""
        INSERT INTO alerts (triggered_at, rule_id, sensor_id, equipment_id, detection_type, severity, message)
        VALUES (NOW(), %s, %s, %s, 'threshold', 'low', 'warm'),
               (NOW(), %s, %s, NULL, 'threshold', 'low', 'orphan')
    """, (rule, t["labelled"], t["eq"], t["gone"], t["gone"]))
    rows = {r["message"]: r for r in _gateway_rows(cur, "_ALERT_SELECT", "a.message")}

    warm = rows["warm"]
    assert (warm["sensor_name"], warm["sensor_display_name"]) == ("temp_root_1", "Root zone")
    assert (warm["equipment_name"], warm["rule_name"], warm["detection_type"]) == ("Pump A", "history rule", "threshold")
    orphan = rows["orphan"]
    assert orphan["sensor_name"] is None and orphan["rule_name"] is None
