# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The migration runner against a real database holding tenants (ADR-0034 dec 2, 4, 8).

The stack applied the sequence through db-migrate, so the ledger must be current. Then every script
is applied again over that live state, migrations and definitions alike, which is what a database
without a ledger does on its first run and what dec 2 promises is safe. A migration edited after it
was applied is refused, and the ledger is left as it was. A pending script on a database holding a
tenant is refused unless a recent backup is recorded in it (ADR-0032 dec 7), written by the backup
script's own recording step.

Requires a running database brought up by db-migrate (db-tenant-isolation.yml stands one up) and
psql; skips when either is missing, and fails instead under IF_REQUIRE_LIVE_STACK=1.
"""
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "infrastructure" / "timescaledb" / "migrate.sh"
SCRIPTS = ROOT / "infrastructure" / "timescaledb" / "init-scripts"
KIND = re.compile(r"^(?:--|#) *migration-kind: *(\w+) *$", re.M)


def _unavailable(reason):
    if os.getenv("IF_REQUIRE_LIVE_STACK") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _env_file() -> dict:
    env = ROOT / ".env"
    if not env.exists():
        return {}
    pairs = (line.split("=", 1) for line in env.read_text().splitlines() if re.match(r"^[A-Z0-9_]+=", line))
    return {k: v for k, v in pairs if k.endswith("_DB_PASSWORD")}


def _run(*args, migrations=SCRIPTS, **extra):
    env = {
        **os.environ,
        **_env_file(),
        "PGHOST": os.getenv("PGHOST", "localhost"),
        "PGPORT": os.getenv("PGPORT", "5432"),
        "PGDATABASE": os.getenv("PGDATABASE", "industryflow"),
        "PGUSER": os.getenv("PGUSER", "postgres"),
        "MIGRATIONS_DIR": str(migrations),
        "MIGRATE_WAIT_SECONDS": "10",
        **extra,
    }
    return subprocess.run(["sh", str(RUNNER), *args], env=env, capture_output=True, text=True, timeout=600)


def _kinds() -> dict:
    return {p.name: KIND.search(p.read_text()).group(1) for p in SCRIPTS.iterdir() if re.match(r"\d", p.name)}


@pytest.fixture(scope="module")
def cur():
    if shutil.which("psql") is None:
        _unavailable("psql not installed")
    try:
        conn = psycopg2.connect(
            host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
            dbname=os.getenv("PGDATABASE", "industryflow"), user=os.getenv("PGUSER", "postgres"),
            password=os.getenv("PGPASSWORD", ""), connect_timeout=5,
        )
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        _unavailable(f"no database reachable: {e}")
    conn.autocommit = True
    c = conn.cursor()
    c.execute("SELECT to_regclass('public.schema_ledger') IS NOT NULL")
    if not c.fetchone()[0]:
        _unavailable("no schema ledger: the stack was not brought up through db-migrate")
    yield c
    conn.close()


@pytest.fixture
def tenant(cur):
    cur.execute("SELECT company_id FROM public.provision_company(%s)", (f"migrate-{uuid.uuid4().hex[:8]}",))
    company_id = cur.fetchone()[0]
    suffix = str(company_id).replace("-", "_")
    yield f"tenant_{suffix}"
    cur.execute(f'DROP SCHEMA IF EXISTS "tenant_{suffix}" CASCADE')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
    cur.execute("DELETE FROM public.companies WHERE company_id = %s", (str(company_id),))


def test_the_ledger_is_current_after_deploy(cur):
    r = _run("verify")
    assert r.returncode == 0, r.stdout + r.stderr
    cur.execute("SELECT script, kind FROM public.schema_ledger")
    assert dict(cur.fetchall()) == _kinds(), "the ledger and the scripts disagree"


def test_every_script_applies_again_over_live_tenants(cur, tenant):
    # Forgetting every migration's row is a database that has never been ledgered; reapply forces
    # every definition. All of it must converge over a live tenant (dec 2).
    kinds = _kinds()
    migrations = [n for n, k in kinds.items() if k == "migration"]
    cur.execute("DELETE FROM public.schema_ledger WHERE script = ANY(%s)", (migrations,))
    # Convergence is under test here, not the backup gate (tested below).
    r = _run("reapply", *[n for n, k in kinds.items() if k == "definition"], MIGRATE_WITHOUT_BACKUP="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{len(kinds)} script(s) applied" in r.stdout, r.stdout
    assert _run("verify").returncode == 0
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = %s "
                "AND table_name = 'sensors' AND column_name = 'display_name'", (tenant,))
    assert cur.fetchone()[0] == 1


def test_a_migration_edited_after_release_is_refused(cur, tmp_path):
    copy = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, copy)
    name = next(n for n, k in sorted(_kinds().items()) if k == "migration")
    with open(copy / name, "a") as f:
        f.write("\n-- edited after release\n")
    cur.execute("SELECT digest, applied_at FROM public.schema_ledger WHERE script = %s", (name,))
    before = cur.fetchone()

    for cmd in ("apply", "verify"):
        r = _run(cmd, migrations=copy)
        assert r.returncode == 2, (cmd, r.stdout, r.stderr)
        assert f"refused {name}: migration changed after it was applied" in r.stderr
    cur.execute("SELECT digest, applied_at FROM public.schema_ledger WHERE script = %s", (name,))
    assert cur.fetchone() == before


def test_a_migration_cannot_be_forced_again(cur):
    name = next(n for n, k in sorted(_kinds().items()) if k == "migration")
    r = _run("reapply", name)
    assert r.returncode == 2 and "is not a definition" in r.stderr


# --- ADR-0032 dec 7: a backup precedes every upgrade -------------------------------------------------

BACKUP = ROOT / "infrastructure" / "backup" / "db-backup.sh"
PROBE = "999-backup-gate-probe.sql"


def _record_backup(stamp):
    """The backup script's own recording step, as a complete run calls it."""
    env = {**os.environ, "PGHOST": os.getenv("PGHOST", "localhost"), "PGPORT": os.getenv("PGPORT", "5432"),
           "PGUSER": os.getenv("PGUSER", "postgres"), "STAMP": stamp, "DATABASES": "industryflow mlflow"}
    r = subprocess.run(["bash", "-c", f'source "{BACKUP}"; log() {{ echo "$*"; }}; record_run'],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.fixture
def upgrade(cur, tenant, tmp_path):
    """A database holding a tenant, and one script pending: an upgrade."""
    copy = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, copy)
    (copy / PROBE).write_text("-- migration-kind: migration\nSELECT 1;\n")
    cur.execute("SELECT to_regclass('public.backup_ledger') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("ALTER TABLE public.backup_ledger RENAME TO backup_ledger_before_test")
    yield copy
    cur.execute("DELETE FROM public.schema_ledger WHERE script = %s", (PROBE,))
    cur.execute("DROP TABLE IF EXISTS public.backup_ledger")
    cur.execute("SELECT to_regclass('public.backup_ledger_before_test') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("ALTER TABLE public.backup_ledger_before_test RENAME TO backup_ledger")


def _probe_applied(cur):
    cur.execute("SELECT count(*) FROM public.schema_ledger WHERE script = %s", (PROBE,))
    return cur.fetchone()[0] == 1


def test_an_upgrade_without_a_recorded_backup_is_refused(cur, upgrade):
    r = _run("apply", migrations=upgrade)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no backup is recorded in this database" in r.stderr
    assert not _probe_applied(cur)


def test_an_upgrade_after_a_stale_backup_is_refused(cur, upgrade):
    _record_backup("20000101T000000Z")
    cur.execute("UPDATE public.backup_ledger SET completed_at = now() - interval '2 hours'")
    r = _run("apply", migrations=upgrade, MIGRATE_BACKUP_MAX_AGE_SECONDS="3600")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "more than 3600s" in r.stderr
    assert not _probe_applied(cur)


def test_an_upgrade_right_after_a_backup_applies(cur, upgrade):
    _record_backup("20000101T000001Z")
    r = _run("apply", migrations=upgrade)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "latest backup completed" in r.stdout
    assert _probe_applied(cur)


def test_the_operator_may_choose_to_upgrade_without_a_backup(cur, upgrade):
    r = _run("apply", migrations=upgrade, MIGRATE_WITHOUT_BACKUP="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING applying without a recent backup" in r.stderr
    assert _probe_applied(cur)


def test_nothing_pending_needs_no_backup(cur, tenant):
    assert _run("apply").returncode == 0
