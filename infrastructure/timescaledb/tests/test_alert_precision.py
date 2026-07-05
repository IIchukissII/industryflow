# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Precision-over-time aggregation proof against a real database (ADR-0022 dec 3).

The api_gateway /label-metrics endpoint derives precision by bucketing alert_labels with
time_bucket + count(*) FILTER. alert_labels is an ordinary table (not a hypertable), so this
proves time_bucket + the FILTER aggregation actually run over it against real TimescaleDB and
produce the expected precision — the DB half of what the endpoint's pure helpers finish.

Skips when no database is reachable, so it never fails a unit-only run.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")


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
def labelled_tenant():
    """A tenant seeded with a known TP/FP/unsure mix for one model."""
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

    model_id = str(uuid.uuid4())
    # 3 true positives, 1 false positive, 1 unsure → precision 0.75, fp-rate 0.25.
    verdicts = ["true_positive"] * 3 + ["false_positive"] + ["unsure"]
    for v in verdicts:
        cur.execute(
            f'INSERT INTO "{schema}".alert_labels (alert_id, verdict, model_id, labeled_by) '
            "VALUES (%s, %s, %s, 'op-1')",
            (str(uuid.uuid4()), v, model_id),
        )

    try:
        yield {"cur": cur, "schema": schema, "model_id": model_id}
    finally:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "{_reader_role(company_id)}"')
        conn.close()


def test_filter_counts_match_the_seeded_mix(labelled_tenant):
    cur, schema, model_id = labelled_tenant["cur"], labelled_tenant["schema"], labelled_tenant["model_id"]
    cur.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE verdict = 'true_positive')  AS tp,
            count(*) FILTER (WHERE verdict = 'false_positive') AS fp,
            count(*) FILTER (WHERE verdict = 'unsure')         AS unsure
        FROM "{schema}".alert_labels
        WHERE model_id = %s
        """,
        (model_id,),
    )
    tp, fp, unsure = cur.fetchone()
    assert (tp, fp, unsure) == (3, 1, 1)
    # The precision the endpoint's helper would compute from these counts.
    assert round(tp / (tp + fp), 4) == 0.75


def test_time_bucket_aggregation_runs_over_the_ordinary_table(labelled_tenant):
    """time_bucket must work on alert_labels even though it is NOT a hypertable."""
    cur, schema, model_id = labelled_tenant["cur"], labelled_tenant["schema"], labelled_tenant["model_id"]
    cur.execute(
        f"""
        SELECT
            time_bucket(CAST('1 day' AS interval), labeled_at) AS bucket,
            count(*) FILTER (WHERE verdict = 'true_positive')  AS tp,
            count(*) FILTER (WHERE verdict = 'false_positive') AS fp
        FROM "{schema}".alert_labels
        WHERE model_id = %s
        GROUP BY bucket
        ORDER BY bucket
        """,
        (model_id,),
    )
    rows = cur.fetchall()
    # All seeded in one run → one bucket carrying the full TP/FP tally.
    assert len(rows) == 1
    _bucket, tp, fp = rows[0]
    assert (tp, fp) == (3, 1)
