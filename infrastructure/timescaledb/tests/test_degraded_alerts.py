# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A degraded ML alert is data, end to end in the tenant store (ADR-0044 dec 3, ADR-0022 dec 2-4).

The worker's own AlertRepository writes the alerts; the gateway's history SELECT, label copy and
upsert and label metrics, read from its source and run on its driver as its role (service_sql.py),
read and label them; the worker's retrain-trigger count runs as written. Proves:

  * the real insert stores degraded and the neutralized features (they were dropped before: the unit
    test that asserted them used a fake repository);
  * an alert from before the change reads NULL (unknown) on both, and nothing infers it;
  * a label copies its alert's degraded state, NULL included;
  * label metrics count labels on degraded alerts apart; the retrain trigger leaves them out and
    still counts the unknown ones.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one up).
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")
asyncpg = pytest.importorskip("asyncpg")

import service_sql  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "alert_service", "worker"))

import repository  # noqa: E402

HISTORY = "services/api_gateway/routers/alerts_history.py"
WORKER = "services/alert_service/worker/repository.py"


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
        yield cur, schema, str(company_id)
    finally:
        cur.execute("SET search_path TO public")
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        conn.close()


def _gateway(schema, name, params, *, sql=None):
    stmt = service_sql.statement(HISTORY, name, sql)
    return service_sql.run(stmt, params, role="api_gateway_user", search_path=f'"{schema}", public')


async def _save(company_id, alerts):
    async def as_worker(conn):
        # The worker's own role, so its grants on the new columns are what is proved.
        await conn.execute('SET ROLE "alert_service_user"')

    s = service_sql._settings()
    pool = await asyncpg.create_pool(ssl=service_sql._ssl(), min_size=1, max_size=2, init=as_worker, **s)
    try:
        repo = repository.AlertRepository(pool)
        for alert in alerts:
            inserted, _ = await repo.save_alert_once(dict(alert, company_id=company_id), window=timedelta(seconds=1))
            assert inserted
    finally:
        await pool.close()


def _alert(**over):
    base = {"alert_id": str(uuid.uuid4()), "triggered_at": datetime.now(timezone.utc).replace(microsecond=0),
            "rule_id": str(uuid.uuid4()), "sensor_id": None, "equipment_id": str(uuid.uuid4()),
            "site_id": "s", "detection_type": "ml", "threshold_value": 0.5, "actual_value": None,
            "condition": "ml_anomaly", "model_id": str(uuid.uuid4()), "anomaly_score": 0.9,
            "severity": "high", "message": "ML anomaly detected"}
    return {**base, **over}


def test_a_degraded_alert_is_stored_read_labelled_and_counted_apart(tenant):
    cur, schema, company_id = tenant
    model = str(uuid.uuid4())
    degraded = _alert(model_id=model, degraded=True, neutralized_features=["a_dev"])
    ordinary = _alert(model_id=model, detection_type="threshold", degraded=False, neutralized_features=[])
    asyncio.run(_save(company_id, [degraded, ordinary]))
    # An alert written before the change: the columns are NULL.
    legacy = str(uuid.uuid4())
    cur.execute(f'INSERT INTO "{schema}".alerts (alert_id, triggered_at, rule_id, detection_type, model_id, '
                "severity, message) VALUES (%s, now(), %s, 'ml', %s, 'high', 'old')",
                (legacy, str(uuid.uuid4()), model))

    sql = service_sql.literal(HISTORY, "_ALERT_SELECT") + " WHERE a.model_id = :model"
    rows = {r["alert_id"]: r for r in _gateway(schema, "_ALERT_SELECT", {"model": uuid.UUID(model)}, sql=sql)}
    assert (rows[degraded["alert_id"]]["degraded"], rows[degraded["alert_id"]]["neutralized_features"]) == (True, ["a_dev"])
    assert (rows[ordinary["alert_id"]]["degraded"], rows[ordinary["alert_id"]]["neutralized_features"]) == (False, [])
    assert (rows[legacy]["degraded"], rows[legacy]["neutralized_features"]) == (None, None)

    # Each is labelled false positive the way the endpoint does it: its fields read, then upserted.
    for alert_id in (degraded["alert_id"], ordinary["alert_id"], legacy):
        [source] = _gateway(schema, "LABEL_ALERT_SELECT", {"alert_id": uuid.UUID(alert_id)})
        [label] = _gateway(schema, "LABEL_UPSERT", {
            "alert_id": uuid.UUID(alert_id), "verdict": "false_positive", "note": None, "labeled_by": "op",
            **{k: source[k] for k in ("triggered_at", "rule_id", "model_id", "detection_type", "severity", "degraded")},
        })
        assert label["degraded"] is source["degraded"]

    template = service_sql.literal(HISTORY, "LABEL_METRICS_SELECT")
    filters = service_sql.literal(HISTORY, "LABEL_METRICS_FILTERS")
    metrics_sql = template.format(where=" AND ".join([filters["days"], filters["model_id"]]))
    [bucket] = _gateway(schema, "LABEL_METRICS_SELECT",
                        {"interval": timedelta(days=1), "days": 30, "model_id": model}, sql=metrics_sql)
    assert (bucket["fp"], bucket["degraded_fp"]) == (2, 1), bucket

    counts = service_sql.run(service_sql.statement(WORKER, "MODEL_LABEL_COUNTS"), (uuid.UUID(model), 30),
                             role="alert_service_user", search_path=f'"{schema}", public')
    assert (counts[0]["tp"], counts[0]["fp"]) == (0, 2), "the trigger counts the ordinary and unknown labels only"


def test_the_migration_is_idempotent(tenant):
    cur, schema, _ = tenant
    cur.execute("SELECT add_degraded_columns_to_alerts(%s)", (schema,))
    cur.execute("SELECT add_degraded_columns_to_alerts(%s)", (schema,))
    cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND column_name IN ('degraded', 'neutralized_features') ORDER BY 1, 2",
                (schema,))
    assert cur.fetchall() == [("alert_labels", "degraded", "boolean"), ("alerts", "degraded", "boolean"),
                              ("alerts", "neutralized_features", "ARRAY")]

