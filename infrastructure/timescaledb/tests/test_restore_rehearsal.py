# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The restore rehearsal against a real TimescaleDB (ADR-0032 dec 6; infrastructure/backup/db-rehearse.sh).

The compose stack's own services run: db-backup dumps both databases with the row counts of the dump's
snapshot, restore-rehearsal restores them into scratch databases and holds them to those counts. A
backup whose recorded counts disagree with what it holds must fail the rehearsal, naming the table,
and the scratch databases must be gone either way.

Needs the compose stack and docker on PATH; db-tenant-isolation.yml provides them. Skips otherwise;
with IF_REQUIRE_LIVE_STACK set a skip is a failure.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

REPO = Path(__file__).resolve().parents[3]
NO_MIRRORS = ["-e", "COLD_SOURCE_BUCKET=", "-e", "MODEL_SOURCE_BUCKET="]


def _skip(reason):
    if os.getenv("IF_REQUIRE_LIVE_STACK"):
        pytest.fail(f"required rehearsal proof could not run: {reason}")
    pytest.skip(reason)


def _connect(dbname):
    conn = psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=dbname,
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )
    conn.autocommit = True
    return conn


def _run(*args):
    return subprocess.run(["docker", "compose", "--profile", "backup", "run", "--rm", "-T", "--no-deps", *args],
                          cwd=REPO, capture_output=True, text=True, timeout=900)


def _scratch_databases():
    conn = _connect("postgres")
    try:
        cur = conn.cursor()
        cur.execute("SELECT datname FROM pg_database WHERE datname LIKE '%\\_rehearsal'")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


@pytest.fixture(scope="module")
def backed_up():
    if shutil.which("docker") is None:
        _skip("docker is not on PATH")
    try:
        _connect("postgres").close()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        _skip(f"no database reachable: {e}")
    r = _run(*NO_MIRRORS, "db-backup")
    assert r.returncode == 0, f"backup failed:\n{r.stdout}\n{r.stderr}"
    time.sleep(1.1)  # backup keys are stamped to the second


def test_the_latest_backup_restores_with_every_table_at_its_dumped_count(backed_up):
    r = _run(*NO_MIRRORS, "restore-rehearsal")
    assert r.returncode == 0, f"rehearsal failed:\n{r.stdout}\n{r.stderr}"
    for db in ("industryflow", "mlflow"):
        assert f"{db}: backup " in r.stdout and "tables match their dumped row counts" in r.stdout, r.stdout
    assert _scratch_databases() == []


def test_a_backup_holding_other_rows_than_it_recorded_fails_the_rehearsal(backed_up):
    # Rewrite the newest recorded count of public.companies, as a dump that lost a row would read.
    tamper = r'''
      set -e
      key=$(aws --endpoint-url "$S3_ENDPOINT_URL" s3api list-objects-v2 --bucket "$S3_BUCKET" \
              --prefix "$S3_PREFIX/counts/industryflow/" --query 'Contents[].Key' --output text \
            | tr '\t' '\n' | sort | tail -n 1)
      aws --endpoint-url "$S3_ENDPOINT_URL" s3 cp "s3://$S3_BUCKET/$key" /tmp/c --only-show-errors
      awk -F'\t' -v OFS='\t' '$1 == "public.companies" { $2 = $2 + 1 } { print }' /tmp/c > /tmp/c2
      grep -q '^public.companies' /tmp/c2
      aws --endpoint-url "$S3_ENDPOINT_URL" s3 cp /tmp/c2 "s3://$S3_BUCKET/$key" --only-show-errors
    '''
    r = _run("--entrypoint", "sh", "db-backup", "-c", tamper)
    assert r.returncode == 0, f"could not rewrite the counts:\n{r.stdout}\n{r.stderr}"
    r = _run(*NO_MIRRORS, "restore-rehearsal")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "industryflow: FAILED, the restored rows differ" in r.stdout
    assert "public.companies" in r.stdout
    assert _scratch_databases() == []
    # Leave a good backup as the newest for whatever runs next.
    assert _run(*NO_MIRRORS, "db-backup").returncode == 0
