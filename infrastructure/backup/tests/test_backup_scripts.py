# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The backup image's scripts, run for real under bash against stand-in `aws`, `psql` and `pg_*`
binaries (fake_aws.py simulates the object stores over a directory).

  * db-restore.sh must fail a restore that pg_restore did not complete. It used to log and succeed
    whatever pg_restore reported, because one error — the timescaledb extension collision — is
    expected; now that collision, and only that collision, is excused.
  * db-backup.sh must back up the cold bucket: once the cold export drops a day from the database,
    the bucket holds the only copy of that raw history.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent

COLLISION = """pg_restore: error: could not execute query: ERROR:  extension "timescaledb" already exists
Command was: CREATE EXTENSION IF NOT EXISTS timescaledb WITH SCHEMA public;
pg_restore: warning: errors ignored on restore: 1
"""
OTHER = """pg_restore: error: could not execute query: ERROR:  relation "sensors" does not exist
Command was: ALTER TABLE ONLY tenant_x.sensor_measurements ADD CONSTRAINT fk FOREIGN KEY (sensor_id) REFERENCES tenant_x.sensors(sensor_id);
"""


def _exe(path: Path, body: str):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
    """A PATH with the stand-ins first, and the environment the scripts require."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    s3 = tmp_path / "s3"
    s3.mkdir()
    _exe(bin_dir / "aws", f"#!{sys.executable}\nimport runpy, sys\n"
                          f"sys.argv[0] = 'aws'\nrunpy.run_path({str(HERE / 'fake_aws.py')!r}, run_name='__main__')\n")
    calls = tmp_path / "psql.log"
    _exe(bin_dir / "psql", f'#!/usr/bin/env bash\necho "$*" >> {calls}\n')
    _exe(bin_dir / "pg_restore",
         '#!/usr/bin/env bash\ncat "$FAKE_PG_RESTORE_STDERR" >&2\nexit "${FAKE_PG_RESTORE_RC:-0}"\n')
    _exe(bin_dir / "pg_dump", '#!/usr/bin/env bash\nwhile [ "$1" != "-f" ]; do shift; done; echo dump > "$2"\n')
    _exe(bin_dir / "pg_dumpall", "#!/usr/bin/env bash\necho '-- globals'\n")
    e = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), "FAKE_S3_ROOT": str(s3),
        "PGHOST": "db", "PGUSER": "postgres", "PGPASSWORD": "pw", "S3_BUCKET": "backups",
        "S3_ENDPOINT_URL": "http://backup-store:9000",
        "AWS_ACCESS_KEY_ID": "backup-writer", "AWS_SECRET_ACCESS_KEY": "backup-secret",
    }
    return {"env": e, "s3": s3, "psql": calls, "tmp": tmp_path}


def _store(env, endpoint):
    return env["s3"] / endpoint.split("://", 1)[-1].replace(":", "_").replace("/", "_")


def _put(env, endpoint, bucket, key, data: bytes):
    path = _store(env, endpoint) / bucket / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _calls(env):
    log = env["s3"] / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# --- db-restore.sh --------------------------------------------------------------------------------

def _restore(env, stderr: str, rc: int):
    (env["tmp"] / "restore.err").write_text(stderr)
    _put(env, env["env"]["S3_ENDPOINT_URL"], "backups", "timescaledb/industryflow/20260101T020000Z.dump", b"x")
    e = dict(env["env"], FAKE_PG_RESTORE_STDERR=str(env["tmp"] / "restore.err"), FAKE_PG_RESTORE_RC=str(rc))
    return subprocess.run(["bash", str(SCRIPTS / "db-restore.sh"), "industryflow", "latest", "--target", "rehearsal"],
                          env=e, capture_output=True, text=True)


def test_restore_succeeds_when_pg_restore_reports_nothing(env):
    assert _restore(env, "", 0).returncode == 0


def test_restore_excuses_only_the_extension_collision(env):
    r = _restore(env, COLLISION, 1)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "only the expected timescaledb extension collision" in r.stdout


def test_restore_fails_on_any_other_error(env):
    r = _restore(env, COLLISION + OTHER, 1)
    assert r.returncode == 1
    assert "do not cut over" in r.stdout
    # Pre-restore mode is still left, so the database is not stranded with its jobs stopped.
    assert "timescaledb_post_restore()" in env["psql"].read_text()


def test_restore_fails_when_pg_restore_fails_without_naming_a_statement(env):
    r = _restore(env, "pg_restore: error: could not read from input file: end of file\n", 1)
    assert r.returncode == 1


@pytest.mark.parametrize("log,benign", [
    (COLLISION, True),
    ("pg_restore: error: could not execute query: ERROR:  something\n"
     "Command was: COMMENT ON EXTENSION timescaledb IS 'x';\n", True),
    (COLLISION + OTHER, False),
    (OTHER, False),
    ("", False),
])
def test_only_extension_collision(env, log, benign):
    path = env["tmp"] / "log"
    path.write_text(log)
    r = subprocess.run(["bash", "-c", f'source {SCRIPTS / "db-restore.sh"}; only_extension_collision "$1"', "_",
                        str(path)], env=env["env"])
    assert (r.returncode == 0) is benign


# --- db-backup.sh: the cold-layer mirror ----------------------------------------------------------

COLD = "http://cold-store:9000"
PART = "tenant_x/year=2026/month=01/day=01/measurements.parquet"
LATE = "tenant_x/year=2026/month=01/day=01/measurements-0001.parquet"
MANIFEST = "tenant_x/year=2026/month=01/day=01/_manifest.json"


def _mirror(env, **extra):
    e = dict(env["env"], COLD_SOURCE_BUCKET="industryflow-cold", COLD_SOURCE_ENDPOINT_URL=COLD,
             COLD_SOURCE_ACCESS_KEY_ID="cold-reader", COLD_SOURCE_SECRET_ACCESS_KEY="cold-read-secret", **extra)
    return subprocess.run(["bash", "-c", f'source {SCRIPTS / "db-backup.sh"}; mirror_cold_bucket'],
                          env=e, capture_output=True, text=True)


def _mirrored(env, key):
    return _store(env, env["env"]["S3_ENDPOINT_URL"]) / "backups" / "cold" / key


def test_the_cold_bucket_is_mirrored_across_stores(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    _put(env, COLD, "industryflow-cold", MANIFEST, b'{"rows": 5}')
    r = _mirror(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mirrored(env, PART).read_bytes() == b"PAR1-day"
    assert _mirrored(env, MANIFEST).read_bytes() == b'{"rows": 5}'
    # The source was read with the read-only principal, the mirror written with the backup one, and
    # neither secret travelled on argv.
    calls = _calls(env)
    assert {c["key_id"] for c in calls if c["endpoint"] == "cold-store_9000"} == {"cold-reader"}
    assert {c["key_id"] for c in calls if c["endpoint"] == "backup-store_9000"} == {"backup-writer"}
    assert not any("secret" in " ".join(c["argv"]) for c in calls)


def test_the_mirror_is_incremental_and_append_only(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    _put(env, COLD, "industryflow-cold", MANIFEST, b'{"rows": 5}')
    assert _mirror(env).returncode == 0
    # A day gains a late part and its manifest grows; the source later loses an object.
    _put(env, COLD, "industryflow-cold", LATE, b"PAR1-late")
    _put(env, COLD, "industryflow-cold", MANIFEST, b'{"rows": 7, "parts": 2}')
    (_store(env, COLD) / "industryflow-cold" / PART).unlink()
    r = _mirror(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mirrored(env, LATE).read_bytes() == b"PAR1-late"
    assert _mirrored(env, MANIFEST).read_bytes() == b'{"rows": 7, "parts": 2}'
    assert _mirrored(env, PART).read_bytes() == b"PAR1-day"  # never deleted from the mirror
    copies = [c for c in _calls(env) if c["argv"][:2] == ["s3", "cp"] and c["argv"][2] == "-"]
    assert len(copies) == 2 + 2  # first run: part + manifest; second: late part + manifest only


def test_a_short_copy_fails_the_backup(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    r = _mirror(env, FAKE_S3_TRUNCATE_PUT="1")
    assert r.returncode != 0
    assert "cold mirror FAILED" in r.stdout


def test_the_backup_run_mirrors_the_cold_bucket(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    e = dict(env["env"], COLD_SOURCE_BUCKET="industryflow-cold", COLD_SOURCE_ENDPOINT_URL=COLD, DATABASES="industryflow")
    r = subprocess.run(["bash", str(SCRIPTS / "db-backup.sh")], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mirrored(env, PART).exists()
    assert list((_store(env, env["env"]["S3_ENDPOINT_URL"]) / "backups" / "timescaledb" / "industryflow").glob("*.dump"))
