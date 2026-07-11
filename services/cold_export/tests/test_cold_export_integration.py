# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
End-to-end proof for the cold-layer exporter (ADR-0025) against a live stack.

The orchestration tests use fakes; this one drives the REAL adapters — psycopg2 to TimescaleDB
(the pg_hba entry + the cold_export_user role + the SECURITY DEFINER drop function) and pyarrow's
S3FileSystem to MinIO — end to end: seed a tenant with old measurements, run the exporter, and
assert the day's Parquet + manifest landed AND the source chunk was dropped, then that a re-run is
a clean no-op. Every one of the three integration defects that real hardware caught (the pyarrow
S3 kwarg, the missing pg_hba entry, the SECURITY DEFINER search_path) would fail this test.

It stands up nothing itself — it expects a live stack via env (TimescaleDB with the init scripts,
MinIO with the cold bucket + write principal). It SKIPS when unreachable; the IF_REQUIRE_LIVE_STACK
guard (conftest.py) turns that skip into a failure in the CI job that stands up the stack.
"""
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("pyarrow")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from cold_export.config import DbConfig, ExportConfig, StoreConfig  # noqa: E402
from cold_export.exporter import run_export  # noqa: E402
from cold_export.naming import company_id_to_prefix, manifest_key, parquet_key  # noqa: E402
from cold_export.source import PostgresMeasurementSource  # noqa: E402
from cold_export.store import S3ColdStore  # noqa: E402

pytestmark = pytest.mark.integration


def _admin():
    return dict(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


def _export_config() -> ExportConfig:
    db = DbConfig(
        host=os.getenv("PGHOST", "localhost"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user="cold_export_user",
        password=os.getenv("COLD_EXPORT_DB_PASSWORD"),
        sslmode=os.getenv("DB_SSLMODE", "require"),
        sslrootcert=os.getenv("DB_SSLROOTCERT", ""),
    )
    store = StoreConfig(
        endpoint=os.getenv("COLD_STORE_ENDPOINT", "http://localhost:9000"),
        access_key=os.getenv("COLD_STORE_ACCESS_KEY"), secret_key=os.getenv("COLD_STORE_SECRET_KEY"),
        region=os.getenv("COLD_STORE_REGION", "us-east-1"),
        bucket=os.getenv("COLD_STORE_BUCKET", "industryflow-cold"),
    )
    return ExportConfig(db=db, store=store, horizon_days=1)


@pytest.fixture(scope="module")
def tenant():
    """A tenant seeded with measurements 3 days old (older than the 1-day horizon), or a skip."""
    try:
        conn = psycopg2.connect(**_admin())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'cold_export_drop_day'")
    if cur.fetchone() is None:
        pytest.skip("cold_export_drop_day() absent — migration 16 not applied")

    cid = uuid.uuid4()
    old_day = (datetime.now(timezone.utc) - timedelta(days=3)).replace(hour=6, minute=0, second=0, microsecond=0)
    schema = None
    try:
        cur.execute("SELECT create_tenant_schema(%s, %s)", (str(cid), f"e2e-cold-{cid}"))
        schema = cur.fetchone()[0]
        cur.execute(f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
                    "VALUES ('pump','unit-1',1) RETURNING equipment_id")
        eqid = cur.fetchone()[0]
        cur.execute(f'INSERT INTO "{schema}".sensors (equipment_id, sensor_name, sensor_type, position) '
                    "VALUES (%s,'s1','temp',1) RETURNING sensor_id", (eqid,))
        sid = cur.fetchone()[0]
        for i in range(5):
            cur.execute(
                f'INSERT INTO "{schema}".sensor_measurements (time, sensor_id, equipment_id, site_id, value, unit, quality_code) '
                "VALUES (%s,%s,%s,'site-1',%s,'C',1)",
                (old_day + timedelta(minutes=i), sid, eqid, 20.0 + i),
            )
        yield {"cur": cur, "cid": str(cid), "schema": schema, "day": old_day.date()}
    finally:
        if schema:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.close()


def _count_day(cur, schema, day: date) -> int:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    cur.execute(f'SELECT count(*) FROM "{schema}".sensor_measurements WHERE time >= %s AND time < %s',
                (start, start + timedelta(days=1)))
    return cur.fetchone()[0]


def test_export_verify_drop_end_to_end(tenant):
    cur, cid, schema, day = tenant["cur"], tenant["cid"], tenant["schema"], tenant["day"]
    assert _count_day(cur, schema, day) == 5  # seeded

    cfg = _export_config()
    if not cfg.db.password:
        pytest.skip("COLD_EXPORT_DB_PASSWORD not set")
    source = PostgresMeasurementSource(cfg.db)
    store = S3ColdStore(cfg.store)

    # Only this tenant is under test, but run_export iterates the whole registry — that's fine, it
    # just also (idempotently) processes any other tenants present.
    run_export(source, store, horizon_days=1, today=date.today())

    # The chunk was dropped ONLY after a verified export.
    assert _count_day(cur, schema, day) == 0
    # The Parquet + manifest exist under the tenant's own (hyphenated) prefix, with the right count.
    prefix = company_id_to_prefix(cid)
    assert store.parquet_row_count(prefix, day) == 5
    assert store.read_manifest(prefix, day) == {"schema": schema, "day": day.isoformat(), "rows": 5}
    # Keep the object-store assertions honest about where the file lives.
    assert parquet_key(prefix, day).startswith(f"tenant_{cid}/")
    assert manifest_key(prefix, day).startswith(f"tenant_{cid}/")

    # Idempotent: a second run drops nothing new and does not error.
    run_export(source, store, horizon_days=1, today=date.today())
    assert _count_day(cur, schema, day) == 0
