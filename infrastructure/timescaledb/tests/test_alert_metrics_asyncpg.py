# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert-label-metrics query, read from the gateway's source and run on the gateway's driver.

This is the regression net for the class of bug that shipped in the label-metrics endpoint: the
query was validated by a psycopg2 test (text protocol, which accepts a string cast to interval),
but the endpoint runs on asyncpg (binary protocol, typed params) where a bucket interval must be
bound as a datetime.timedelta, not a string — so the psycopg2 test passed while the endpoint 500'd.

Here the endpoint's statement and filters, composed the way the endpoint composes them, run on
SQLAlchemy over asyncpg as the gateway's role with the endpoint's parameter types (service_sql.py).

Requires a running database with the init scripts applied (the db-tenant-isolation CI stands one
up). Skips when no database is reachable, so it never fails a unit-only run.
"""
import os
import uuid
from datetime import timedelta

import pytest

psycopg2 = pytest.importorskip("psycopg2")

import service_sql  # noqa: E402

# Watched by db-tenant-isolation.yml's path filter (tests/config_guards/test_ci_path_filters.py).
ENDPOINT = "services/api_gateway/routers/alerts_history.py"


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


def _metrics(schema, **filters):
    """The endpoint's query with ``filters`` (its optional model_id / rule_id, as the str it takes)."""
    template = service_sql.literal(ENDPOINT, "LABEL_METRICS_SELECT")
    clauses = service_sql.literal(ENDPOINT, "LABEL_METRICS_FILTERS")
    where = [clauses["days"]] + [clauses[name] for name in filters]
    stmt = service_sql.statement(ENDPOINT, "LABEL_METRICS_SELECT", template.format(where=" AND ".join(where)))
    # The bucket interval is a timedelta (_BUCKET_INTERVAL), the window an int, as the endpoint binds them.
    params = {"interval": timedelta(days=1), "days": 30, **filters}
    return service_sql.run(stmt, params, role="api_gateway_user", search_path=f'"{schema}", public')


def test_label_metrics_aggregation_on_the_gateways_driver(tenant):
    cur, schema = tenant
    cur.execute(f'SET search_path TO "{schema}", public')
    model_id, rule_id = uuid.uuid4(), uuid.uuid4()
    # 3 true positives, 1 false positive, 1 unsure → precision 0.75; one more label on another model.
    for v in (["true_positive"] * 3 + ["false_positive", "unsure"]):
        cur.execute("INSERT INTO alert_labels (alert_id, verdict, model_id, rule_id, labeled_by) "
                    "VALUES (%s, %s, %s, %s, 'op')", (str(uuid.uuid4()), v, str(model_id), str(rule_id)))
    cur.execute("INSERT INTO alert_labels (alert_id, verdict, model_id, labeled_by) "
                "VALUES (%s, 'false_positive', %s, 'op')", (str(uuid.uuid4()), str(uuid.uuid4())))

    rows = _metrics(schema, model_id=str(model_id))
    assert len(rows) == 1, "same-run labels should fall in one daily bucket"
    r = rows[0]
    assert (r["tp"], r["fp"], r["unsure"]) == (3, 1, 1)
    assert round(r["tp"] / (r["tp"] + r["fp"]), 4) == 0.75
    assert [(x["tp"], x["fp"]) for x in _metrics(schema, rule_id=str(rule_id))] == [(3, 1)]
    assert sum(x["fp"] for x in _metrics(schema)) == 2
