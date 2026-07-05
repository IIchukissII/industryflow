# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Integration test for the alert-label-metrics query through **asyncpg** — the api_gateway's
actual driver.

This is the regression net for the class of bug that shipped in the label-metrics endpoint: the
query was validated by a psycopg2 test (text protocol, which accepts a string cast to interval),
but the endpoint runs on asyncpg (binary protocol, typed params) where a bucket interval must be
bound as a datetime.timedelta, not a string — so the psycopg2 test passed while the endpoint 500'd.

Here we run the same precision aggregation the endpoint runs, via asyncpg, so an asyncpg-incompatible
change to that query fails in CI rather than only at runtime.

Requires a running database with the init scripts applied (the db-tenant-isolation CI stands one
up). Skips when no database is reachable, so it never fails a unit-only run.
"""
import asyncio
import os
import ssl as ssl_mod
import uuid
from datetime import timedelta

import pytest

asyncpg = pytest.importorskip("asyncpg")


def _ssl():
    # CI uses a plain loopback DB (no PGSSLMODE); a TLS-enforced DB (ADR-0017) sets PGSSLMODE.
    mode = (os.getenv("PGSSLMODE") or "").lower()
    if mode in ("require", "prefer", "allow"):
        ctx = ssl_mod.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl_mod.CERT_NONE
        return ctx
    if mode in ("verify-ca", "verify-full"):
        return ssl_mod.create_default_context(cafile=os.getenv("PGSSLROOTCERT") or None)
    return None


def _dsn():
    return dict(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        database=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        ssl=_ssl(),
    )


async def _connect():
    try:
        return await asyncio.wait_for(asyncpg.connect(**_dsn()), timeout=5)
    except Exception as e:  # noqa: BLE001 - any failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")


async def _run():
    conn = await _connect()
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_proc WHERE proname = 'create_tenant_schema'"):
            pytest.skip("create_tenant_schema() not present — init scripts not applied")

        company_id = uuid.uuid4()
        schema = await conn.fetchval("SELECT create_tenant_schema($1, $2)", str(company_id), f"test-{company_id}")
        model_id = uuid.uuid4()
        try:
            await conn.execute(f'SET search_path TO "{schema}", public')
            # 3 true positives, 1 false positive, 1 unsure → precision 0.75.
            for v in (["true_positive"] * 3 + ["false_positive", "unsure"]):
                await conn.execute(
                    "INSERT INTO alert_labels (alert_id, verdict, model_id, labeled_by) VALUES ($1, $2, $3, 'op')",
                    uuid.uuid4(), v, model_id,
                )

            # The endpoint's aggregation, via asyncpg: the bucket interval is bound as a timedelta
            # (a string here would raise DataError — the exact bug this guards), the window as an int.
            rows = await conn.fetch(
                """
                SELECT
                    time_bucket($1, labeled_at) AS bucket,
                    count(*) FILTER (WHERE verdict = 'true_positive')  AS tp,
                    count(*) FILTER (WHERE verdict = 'false_positive') AS fp,
                    count(*) FILTER (WHERE verdict = 'unsure')         AS unsure
                FROM alert_labels
                WHERE model_id = $2
                  AND labeled_at >= NOW() - make_interval(days => $3::int)
                GROUP BY bucket
                ORDER BY bucket
                """,
                timedelta(days=1), model_id, 30,
            )
            assert len(rows) == 1, "same-run labels should fall in one daily bucket"
            r = rows[0]
            assert (r["tp"], r["fp"], r["unsure"]) == (3, 1, 1)
            precision = round(r["tp"] / (r["tp"] + r["fp"]), 4)
            assert precision == 0.75
        finally:
            await conn.execute("RESET search_path")
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await conn.execute(f'DROP ROLE IF EXISTS "tenant_reader_{str(company_id).replace("-", "_")}"')
    finally:
        await conn.close()


def test_label_metrics_aggregation_via_asyncpg():
    asyncio.run(_run())
