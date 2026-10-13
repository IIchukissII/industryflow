# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
End-to-end proof for the cold-layer exporter (ADR-0025) against a live stack.

The orchestration tests use fakes; this one drives the REAL adapters — psycopg2 to TimescaleDB
(the pg_hba entry + the cold_export_user role + the fingerprint and SECURITY DEFINER drop functions) and
pyarrow's S3FileSystem to MinIO — end to end: seed a tenant with old measurements, run the exporter,
and assert the day's Parquet + manifest landed AND the source chunk was dropped, then that a re-run
is a clean no-op. Every one of the three integration defects that real hardware caught (the pyarrow
S3 kwarg, the missing pg_hba entry, the SECURITY DEFINER search_path) would fail this test.

It also proves what only a real database can: rows arriving for an exported-and-dropped day become
a further part without touching the archive; a row inserted between the verify and the drop is not
dropped unexported; and the drop function refuses a day that is not the exported snapshot.

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
from cold_export.naming import company_id_to_prefix, manifest_key, parquet_key, part_file  # noqa: E402
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


@pytest.fixture
def admin_cur():
    try:
        conn = psycopg2.connect(**_admin())
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'cold_export_day_fingerprint'")
    if cur.fetchone() is None:
        pytest.skip("cold_export_day_fingerprint() absent — migration 16 not applied")
    yield cur
    conn.close()


@pytest.fixture
def tenant(admin_cur):
    """A fresh tenant seeded with 5 measurements 3 days old (older than the 1-day horizon)."""
    cur = admin_cur
    cid = uuid.uuid4()
    old_day = (datetime.now(timezone.utc) - timedelta(days=3)).replace(hour=6, minute=0, second=0, microsecond=0)
    schema = None
    try:
        cur.execute("SELECT create_tenant_schema(%s, %s)", (str(cid), f"e2e-cold-{cid}"))
        schema = cur.fetchone()[0]
        # The exporter enumerates public.companies (verified identity, ADR-0025 dec 5), but
        # create_tenant_schema only UPDATEs that row — it does not insert it. Register the tenant so
        # run_export sees it (this is what the app's signup flow does).
        cur.execute(
            "INSERT INTO public.companies (company_id, company_name, schema_name) VALUES (%s,%s,%s) "
            "ON CONFLICT (company_id) DO UPDATE SET schema_name = EXCLUDED.schema_name",
            (str(cid), f"e2e-cold-{cid}", schema),
        )
        cur.execute(f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
                    "VALUES ('pump','unit-1',1) RETURNING equipment_id")
        eqid = cur.fetchone()[0]
        cur.execute(f'INSERT INTO "{schema}".sensors (equipment_id, sensor_name, sensor_type, position) '
                    "VALUES (%s,'s1','temp',1) RETURNING sensor_id", (eqid,))
        sid = cur.fetchone()[0]
        t = {"cur": cur, "cid": str(cid), "schema": schema, "day": old_day.date(),
             "sid": sid, "eqid": eqid, "base": old_day}
        _insert(t, range(5))
        yield t
    finally:
        if schema:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute("DELETE FROM public.cold_export_drops WHERE schema_name = %s", (schema,))
        cur.execute("DELETE FROM public.companies WHERE company_id = %s", (str(cid),))


def _insert(t, minutes, cur=None):
    cur = cur or t["cur"]
    for i in minutes:
        cur.execute(
            f'INSERT INTO "{t["schema"]}".sensor_measurements '
            "(time, sensor_id, equipment_id, site_id, value, unit, quality_code) "
            "VALUES (%s,%s,%s,'site-1',%s,'C',1)",
            (t["base"] + timedelta(minutes=i), t["sid"], t["eqid"], 20.0 + i),
        )


def _count_day(cur, schema, day: date) -> int:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    cur.execute(f'SELECT count(*) FROM "{schema}".sensor_measurements WHERE time >= %s AND time < %s',
                (start, start + timedelta(days=1)))
    return cur.fetchone()[0]


def _adapters():
    cfg = _export_config()
    if not cfg.db.password:
        pytest.skip("COLD_EXPORT_DB_PASSWORD not set")
    return PostgresMeasurementSource(cfg.db), S3ColdStore(cfg.store)


def test_export_verify_drop_end_to_end(tenant):
    cur, cid, schema, day = tenant["cur"], tenant["cid"], tenant["schema"], tenant["day"]
    assert _count_day(cur, schema, day) == 5  # seeded
    source, store = _adapters()

    # Only this tenant is under test, but run_export iterates the whole registry — that's fine, it
    # just also (idempotently) processes any other tenants present.
    run_export(source, store, horizon_days=1, today=date.today())

    # The chunk was dropped ONLY after a verified export.
    assert _count_day(cur, schema, day) == 0
    # The Parquet + manifest exist under the tenant's own (hyphenated) prefix, with the right count.
    prefix = company_id_to_prefix(cid)
    assert store.part_row_count(prefix, day, part_file(0)) == 5
    assert store.read_manifest(prefix, day) == {
        "format": 2, "schema": schema, "day": day.isoformat(), "rows": 5,
        "parts": [{"file": part_file(0), "rows": 5}],
    }
    # The database recorded the drop, in the drop's own transaction.
    assert source.drop_recorded(schema, day, part_file(0))
    # Keep the object-store assertions honest about where the file lives.
    assert parquet_key(prefix, day).startswith(f"tenant_{cid}/")
    assert manifest_key(prefix, day).startswith(f"tenant_{cid}/")

    # Idempotent: a second run drops nothing new and does not error.
    run_export(source, store, horizon_days=1, today=date.today())
    assert _count_day(cur, schema, day) == 0


def test_late_rows_after_drop_become_a_second_part(tenant):
    cur, cid, schema, day = tenant["cur"], tenant["cid"], tenant["schema"], tenant["day"]
    source, store = _adapters()
    prefix = company_id_to_prefix(cid)
    run_export(source, store, horizon_days=1, today=date.today())
    assert _count_day(cur, schema, day) == 0

    # Readings for the exported day arrive late: they land in a fresh chunk.
    _insert(tenant, range(100, 102))
    run_export(source, store, horizon_days=1, today=date.today())

    # The archived part is intact and the late rows are a second, separately verified part.
    assert store.part_row_count(prefix, day, part_file(0)) == 5
    assert store.part_row_count(prefix, day, part_file(1)) == 2
    manifest = store.read_manifest(prefix, day)
    assert manifest["parts"] == [{"file": part_file(0), "rows": 5}, {"file": part_file(1), "rows": 2}]
    assert manifest["rows"] == 7
    assert _count_day(cur, schema, day) == 0


def test_insert_between_verify_and_drop_is_not_dropped(tenant):
    cur, cid, schema, day = tenant["cur"], tenant["cid"], tenant["schema"], tenant["day"]
    source, store = _adapters()
    prefix = company_id_to_prefix(cid)

    class RacingStore:
        """The real store; another session inserts into the day once the part is listed."""

        def __getattr__(self, name):
            return getattr(store, name)

        def write_manifest(self, prefix_, day_, manifest):
            store.write_manifest(prefix_, day_, manifest)
            if prefix_ == prefix:
                _insert(tenant, [200])

    # The drop sees the day is no longer the exported snapshot and drops nothing.
    run_export(source, RacingStore(), horizon_days=1, today=date.today())
    assert _count_day(cur, schema, day) == 6
    assert store.read_manifest(prefix, day)["rows"] == 5
    assert not source.drop_recorded(schema, day, part_file(0))

    # The next run re-exports the pending part with the racing row, then drops.
    run_export(source, store, horizon_days=1, today=date.today())
    assert store.read_manifest(prefix, day)["parts"] == [{"file": part_file(0), "rows": 6}]
    assert store.part_row_count(prefix, day, part_file(0)) == 6
    assert _count_day(cur, schema, day) == 0


def test_drop_refuses_a_day_that_is_not_the_exported_snapshot(tenant):
    schema, day = tenant["schema"], tenant["day"]
    cfg = _export_config()
    if not cfg.db.password:
        pytest.skip("COLD_EXPORT_DB_PASSWORD not set")
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    conn = psycopg2.connect(**cfg.db.dsn_kwargs())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT rows, fingerprint FROM public.cold_export_day_fingerprint(%s,%s,%s)",
                        (schema, start, end))
            rows, fingerprint = cur.fetchone()
            assert rows == 5
            # Same count, different rows: the fingerprint refuses it.
            cur.execute("SELECT cold_export_drop_day(%s,%s,%s,%s,%s,%s)",
                        (schema, start, end, 5, fingerprint + 1, part_file(0)))
            assert cur.fetchone()[0] is False
            # A different count: refused.
            cur.execute("SELECT cold_export_drop_day(%s,%s,%s,%s,%s,%s)",
                        (schema, start, end, 4, fingerprint, part_file(0)))
            assert cur.fetchone()[0] is False
        conn.rollback()
        # Not READ COMMITTED: a snapshot taken before the lock could miss rows, so it refuses outright.
        conn.set_session(isolation_level=psycopg2.extensions.ISOLATION_LEVEL_REPEATABLE_READ)
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error, match="READ COMMITTED"):
                cur.execute("SELECT cold_export_drop_day(%s,%s,%s,%s,%s,%s)",
                            (schema, start, end, 5, fingerprint, part_file(0)))
        conn.rollback()
    finally:
        conn.close()
    assert _count_day(tenant["cur"], schema, day) == 5
