# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Live proof that the alert worker's cooldown holds across concurrent writers (ADR-0005 dec 6).

Several writers — standing in for worker replicas, or a redelivery racing the original — insert
alerts in one dedup scope at different event times inside one cooldown window, all at once. Under
READ COMMITTED each would find no prior row and insert; the worker's advisory lock must leave
exactly one. Also proves the tenant search_path does not survive the checkout (ADR-0003 dec 6).

Runs the worker's own AlertRepository against a tenant built by create_tenant_schema(). Skips when
no database is reachable, so it never fails a unit-only run.
"""
import asyncio
import os
import ssl as ssl_mod
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

asyncpg = pytest.importorskip("asyncpg")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "alert_service", "worker"))

from repository import AlertRepository  # noqa: E402

WRITERS = 8


def _ssl():
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


async def _pool():
    try:
        return await asyncio.wait_for(
            asyncpg.create_pool(**_dsn(), min_size=WRITERS, max_size=WRITERS), timeout=10
        )
    except Exception as e:  # noqa: BLE001 - any failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")


async def _run():
    pool = await _pool()
    try:
        async with pool.acquire() as conn:
            if not await conn.fetchval("SELECT 1 FROM pg_proc WHERE proname = 'create_tenant_schema'"):
                pytest.skip("create_tenant_schema() not present — init scripts not applied")
            company_id = uuid.uuid4()
            schema = await conn.fetchval("SELECT create_tenant_schema($1, $2)", str(company_id), f"test-{company_id}")

        try:
            repo = AlertRepository(pool)
            rule_id, sensor_id = str(uuid.uuid4()), str(uuid.uuid4())
            t0 = datetime.now(timezone.utc).replace(microsecond=0)

            def alert(i):
                return {
                    "company_id": str(company_id), "alert_id": str(uuid.uuid4()),
                    "triggered_at": t0 + timedelta(seconds=i), "rule_id": rule_id,
                    "sensor_id": sensor_id, "equipment_id": None, "detection_type": "threshold",
                    "severity": "high", "message": f"writer {i}",
                }

            results = await asyncio.gather(*(
                repo.save_alert_once(alert(i), window=timedelta(seconds=300)) for i in range(WRITERS)
            ))

            # Every pooled connection the writers used must be back on the default path.
            conns = [await pool.acquire() for _ in range(WRITERS)]
            try:
                paths = {await c.fetchval("SHOW search_path") for c in conns}
                rows = await conns[0].fetchval(
                    f'SELECT count(*) FROM "{schema}".alerts WHERE rule_id = $1', uuid.UUID(rule_id)
                )
            finally:
                for c in conns:
                    await pool.release(c)
            return results, rows, paths
        finally:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                await conn.execute(f'DROP ROLE IF EXISTS "tenant_reader_{str(company_id).replace("-", "_")}"')
    finally:
        await pool.close()


def test_concurrent_writers_in_one_scope_leave_one_alert_and_no_tenant_path():
    results, rows, paths = asyncio.run(_run())
    assert sum(1 for inserted, _ in results if inserted) == 1
    assert rows == 1
    assert not any("tenant_" in p for p in paths), paths
