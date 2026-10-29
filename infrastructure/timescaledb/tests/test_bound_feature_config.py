# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A model's feature configuration is fixed once bound (init script 30, ADR-0041).

Provisions a tenant through the real ``create_tenant_schema()`` and proves, as the ML service's own
role so the rule binds the writer that edits configurations:

  * an edit to a bound configuration's sensors or transforms is refused, its name is not,
  * deleting a bound configuration is refused and leaves the model bound,
  * a model's binding cannot be changed,
  * an unbound configuration, or one whose last model was removed, is edited and deleted freely,
  * the ML service's query for the models that bind a config, read from its source and run here
    unchanged, names them,
  * re-applying the migration function is a no-op.

Requires a running database with the init scripts applied (the CI workflow stands one up via docker
compose). When no database is reachable it skips.
"""
import ast
import json
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
# Watched by db-tenant-isolation.yml's path filter (tests/config_guards/test_ci_path_filters.py).
REPOSITORY = "services/ml_service/api/repository.py"


def _repository_sql(name):
    tree = ast.parse(open(os.path.join(_ROOT, REPOSITORY)).read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {REPOSITORY}")


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

    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'add_bound_feature_config_rules'")
    if cur.fetchone() is None:
        pytest.skip("add_bound_feature_config_rules() not present — init scripts not applied")

    company_id = uuid.uuid4()
    cur.execute("SELECT create_tenant_schema(%s, %s)", (str(company_id), f"test-{company_id}"))
    schema = cur.fetchone()[0]
    suffix = str(company_id).replace("-", "_")
    try:
        yield {"cur": cur, "schema": schema}
    finally:
        cur.execute("RESET ROLE")
        cur.execute("SET search_path TO public")
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        conn.close()


@pytest.fixture
def cur(tenant):
    cur = tenant["cur"]
    cur.execute("SET ROLE ml_service_user")
    cur.execute(f'SET search_path TO "{tenant["schema"]}", public')
    try:
        yield cur
    finally:
        cur.execute("RESET ROLE")


def _config(cur, sensors=("temp_1",)):
    cur.execute(
        "INSERT INTO feature_engineering_configs (equipment_type, name, base_sensors, transformations) "
        "VALUES ('pump', %s, %s, %s) RETURNING id",
        (f"cfg-{uuid.uuid4()}", json.dumps(list(sensors)),
         json.dumps([{"name": "temp_1_raw", "type": "raw", "sensor": "temp_1"}])),
    )
    return cur.fetchone()[0]


def _model(cur, config_id):
    cur.execute(
        "INSERT INTO ml_models (model_name, model_type, feature_config_id) VALUES (%s, 'isolation_forest', %s) "
        "RETURNING model_id",
        (f"m-{uuid.uuid4()}", config_id),
    )
    return cur.fetchone()[0]


def test_a_bound_configuration_keeps_its_sensors_and_transforms(cur):
    config_id = _config(cur)
    _model(cur, config_id)
    with pytest.raises(errors.RestrictViolation, match="ADR-0041 dec 2"):
        cur.execute("UPDATE feature_engineering_configs SET base_sensors = '[\"temp_1\", \"flow_1\"]' "
                    "WHERE id = %s", (config_id,))
    with pytest.raises(errors.RestrictViolation):
        cur.execute("UPDATE feature_engineering_configs SET transformations = '[]' WHERE id = %s", (config_id,))


def test_a_bound_configuration_is_still_renamed(cur):
    config_id = _config(cur)
    _model(cur, config_id)
    cur.execute("UPDATE feature_engineering_configs SET name = %s, description = 'x', equipment_type = 'valve' "
                "WHERE id = %s", (f"renamed-{uuid.uuid4()}", config_id))
    assert cur.rowcount == 1


def test_a_bound_configuration_is_not_deleted_and_its_model_stays_bound(cur):
    config_id = _config(cur)
    model_id = _model(cur, config_id)
    with pytest.raises(errors.ForeignKeyViolation):
        cur.execute("DELETE FROM feature_engineering_configs WHERE id = %s", (config_id,))
    cur.execute("SELECT feature_config_id FROM ml_models WHERE model_id = %s", (model_id,))
    assert cur.fetchone()[0] == config_id


def test_a_binding_is_fixed(cur):
    model_id = _model(cur, _config(cur))
    other = _config(cur)
    with pytest.raises(errors.RestrictViolation, match="ADR-0041 dec 1"):
        cur.execute("UPDATE ml_models SET feature_config_id = %s WHERE model_id = %s", (other, model_id))
    with pytest.raises(errors.RestrictViolation):
        cur.execute("UPDATE ml_models SET feature_config_id = NULL WHERE model_id = %s", (model_id,))


def test_an_unbound_configuration_is_edited_and_deleted(cur):
    config_id = _config(cur)
    cur.execute("UPDATE feature_engineering_configs SET base_sensors = '[\"flow_1\"]' WHERE id = %s", (config_id,))
    cur.execute("DELETE FROM feature_engineering_configs WHERE id = %s", (config_id,))
    assert cur.rowcount == 1


def test_removing_the_last_model_frees_the_configuration(cur):
    config_id = _config(cur)
    model_id = _model(cur, config_id)
    cur.execute("DELETE FROM ml_models WHERE model_id = %s", (model_id,))
    cur.execute("UPDATE feature_engineering_configs SET base_sensors = '[\"flow_1\"]' WHERE id = %s", (config_id,))
    cur.execute("DELETE FROM feature_engineering_configs WHERE id = %s", (config_id,))
    assert cur.rowcount == 1


def test_the_services_query_names_the_binding_models(cur):
    config_id = _config(cur)
    model_id = _model(cur, config_id)
    cur.execute(_repository_sql("BOUND_MODELS_SELECT").replace("$1", "%s"), (config_id,))
    assert [r[0] for r in cur.fetchall()] == [model_id]


def test_the_migration_function_is_idempotent(tenant):
    cur = tenant["cur"]
    cur.execute("SELECT add_bound_feature_config_rules(%s)", (tenant["schema"],))
    cur.execute("SELECT add_bound_feature_config_rules(%s)", (tenant["schema"],))
    cur.execute(
        "SELECT confdeltype FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'f' "
        "AND confrelid = %s::regclass",
        (f'"{tenant["schema"]}".ml_models', f'"{tenant["schema"]}".feature_engineering_configs'),
    )
    assert [r[0] for r in cur.fetchall()] == ["r"]
