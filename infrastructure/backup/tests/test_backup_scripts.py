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
TOC = """;
; Archive created at 2026-01-01 02:00:00 UTC
;
4321; 1262 16384 DATABASE - industryflow postgres
4322; 0 0 DATABASE PROPERTIES - industryflow postgres
4323; 0 0 ACL - DATABASE industryflow postgres
2; 3079 16385 EXTENSION - timescaledb
4324; 0 0 COMMENT - EXTENSION timescaledb
230; 1259 16400 TABLE public companies postgres
4325; 0 0 ACL public TABLE companies postgres
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
    # The role check (-f …/roles.sql) prints $FAKE_PSQL_REFUSED: nothing means every role exists.
    _exe(bin_dir / "psql", f'#!/usr/bin/env bash\necho "$*" >> {calls}\n'
                           'case "$*" in *roles.sql*) printf "%s" "${FAKE_PSQL_REFUSED:-}" ;; esac\n')
    # -l prints the table of contents; -f writes the script (-L: the database-level one); otherwise
    # it is the restore itself.
    toc = tmp_path / "toc.list"
    toc.write_text(TOC)
    _exe(bin_dir / "pg_restore", f'#!/usr/bin/env bash\necho "$*" >> {tmp_path / "pg_restore.log"}\n'
         'out=; list=; prev=\n'
         'for a; do case "$a" in -l) cat "${FAKE_PG_RESTORE_TOC:-' + str(toc) + '}"; exit 0 ;; esac\n'
         '  [ "$prev" = -f ] && out="$a"; [ "$prev" = -L ] && list="$a"; prev="$a"; done\n'
         'if [ -n "$out" ]; then\n'
         '  if [ -n "$list" ]; then printf "%s" "${FAKE_PG_RESTORE_DB_SQL:-}" > "$out"\n'
         '  else printf "%s" "${FAKE_PG_RESTORE_SQL:-}" > "$out"; fi\n'
         '  exit 0\nfi\n'
         'cat "$FAKE_PG_RESTORE_STDERR" >&2\nexit "${FAKE_PG_RESTORE_RC:-0}"\n')
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

def _restore(env, stderr: str, rc: int, **extra):
    (env["tmp"] / "restore.err").write_text(stderr)
    _put(env, env["env"]["S3_ENDPOINT_URL"], "backups", "timescaledb/industryflow/20260101T020000Z.dump", b"x")
    e = dict(env["env"], FAKE_PG_RESTORE_STDERR=str(env["tmp"] / "restore.err"), FAKE_PG_RESTORE_RC=str(rc),
             **extra)
    return subprocess.run(["bash", str(SCRIPTS / "db-restore.sh"), "industryflow", "latest", "--target", "rehearsal"],
                          env=e, capture_output=True, text=True)


def test_restore_succeeds_when_pg_restore_reports_nothing(env):
    assert _restore(env, "", 0).returncode == 0


def test_restore_keeps_owners_and_privileges(env):
    r = _restore(env, "", 0)
    assert r.returncode == 0, r.stdout + r.stderr
    # The restore itself (the call with -d) passes neither flag that drops owners or grants.
    restore = [c for c in (env["tmp"] / "pg_restore.log").read_text().splitlines() if " -d " in f" {c} "]
    assert len(restore) == 1, restore
    assert "--no-owner" not in restore[0] and "--no-privileges" not in restore[0] and " -O" not in restore[0]
    assert " -x" not in f" {restore[0]}"
    assert 'CREATE DATABASE "rehearsal" OWNER "postgres";' in env["psql"].read_text()


def test_restore_refuses_a_dump_naming_a_missing_role_before_touching_the_target(env):
    r = _restore(env, "", 0, FAKE_PSQL_REFUSED="missing role: tenant_reader_x\n")
    assert r.returncode == 1
    assert "missing role: tenant_reader_x" in r.stdout
    assert "DROP DATABASE" not in env["psql"].read_text()


def test_restore_applies_database_level_grants_to_the_target(env):
    r = _restore(env, "", 0, FAKE_PG_RESTORE_DB_SQL="GRANT ALL ON DATABASE industryflow TO mlflow_user;\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restoring database-level grants" in r.stdout


def test_restore_skips_timescaledb_steps_for_a_plain_database(env):
    toc = env["tmp"] / "plain.list"
    toc.write_text("4321; 1262 16384 DATABASE - mlflow postgres\n")
    r = _restore(env, "", 0, FAKE_PG_RESTORE_TOC=str(toc))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "timescaledb" not in env["psql"].read_text()


def _sourced(env, call, *args):
    return subprocess.run(["bash", "-c", f'source {SCRIPTS / "db-restore.sh"}; {call} "$@"', "_", *args],
                          env=env["env"], capture_output=True, text=True)


def test_dump_roles_names_every_owner_grantee_grantor_and_default_privilege_role(env):
    sql = env["tmp"] / "schema.sql"
    sql.write_text("\n".join([
        "ALTER TABLE public.companies OWNER TO postgres;",
        'ALTER SCHEMA tenant_a OWNER TO "Weird ""Owner""";',
        "GRANT SELECT ON TABLE public.companies TO api_gateway_user;",
        'GRANT SELECT,INSERT ON TABLE tenant_a.sensors TO "tenant_reader_a";',
        "GRANT ALL ON SCHEMA public TO mlflow_user WITH GRANT OPTION;",
        "REVOKE ALL ON FUNCTION public.provision_company(p_company_name text, p_is_active boolean) FROM PUBLIC;",
        "ALTER DEFAULT PRIVILEGES FOR ROLE api_gateway_user IN SCHEMA public GRANT SELECT ON TABLES TO ml_service_user;",
        "GRANT SELECT ON TABLE x.y TO cold_export_user GRANTED BY spark_streaming_user;",
        "    EXECUTE format('GRANT USAGE ON SCHEMA %I TO body_only_user', v);",
        "",
    ]))
    r = _sourced(env, "dump_roles", str(sql))
    assert r.returncode == 0, r.stderr
    assert sorted(r.stdout.splitlines()) == sorted([
        "postgres", 'Weird "Owner"', "api_gateway_user", "tenant_reader_a", "mlflow_user",
        "ml_service_user", "cold_export_user", "spark_streaming_user"])


def test_retarget_database_rewrites_only_the_source_database(env):
    sql = env["tmp"] / "db.sql"
    # As pg_restore -C writes it: the database's own CREATE and \connect come whatever -L lists.
    sql.write_text("CREATE DATABASE mlflow WITH TEMPLATE = template0 ENCODING = 'UTF8';\n"
                   "\\connect mlflow\n"
                   'GRANT ALL ON DATABASE mlflow TO mlflow_user;\nALTER DATABASE "mlflow" SET x TO 1;\n'
                   "GRANT ALL ON DATABASE mlflow2 TO a;\nSET row_security = off;\n")
    r = _sourced(env, "retarget_database", "mlflow", "mlflow_rehearsal", str(sql))
    assert r.stdout.splitlines() == [
        'GRANT ALL ON DATABASE "mlflow_rehearsal" TO mlflow_user;',
        'ALTER DATABASE "mlflow_rehearsal" SET x TO 1;',
        "GRANT ALL ON DATABASE mlflow2 TO a;",
        "SET row_security = off;",
    ]


def test_database_level_entries_and_owner(env):
    toc = env["tmp"] / "t.list"
    toc.write_text(TOC)
    entries = _sourced(env, "database_level_entries", str(toc)).stdout.splitlines()
    assert [e.split(" ", 3)[3] for e in entries] == [
        "DATABASE PROPERTIES - industryflow postgres", "ACL - DATABASE industryflow postgres"]
    assert _sourced(env, "database_owner", str(toc)).stdout == "postgres\n"


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
    e = dict(env["env"], COLD_SOURCE_BUCKET="industryflow-cold", SOURCE_ENDPOINT_URL=COLD,
             SOURCE_ACCESS_KEY_ID="cold-reader", SOURCE_SECRET_ACCESS_KEY="cold-read-secret", **extra)
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


def test_the_mirror_is_incremental(env):
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
    assert _mirrored(env, PART).read_bytes() == b"PAR1-day"  # kept: gone from the source only now
    copies = [c for c in _calls(env) if c["argv"][:2] == ["s3", "cp"] and c["argv"][2] == "-"]
    assert len(copies) == 2 + 2  # first run: part + manifest; second: late part + manifest only


DAY = 86400
T0 = 1_760_000_000


def _absent(env):
    path = _mirrored(env, "_absent.tsv")
    return dict(line.split("\t") for line in path.read_text().splitlines()) if path.exists() else {}


def test_an_object_gone_from_the_source_leaves_the_mirror_after_one_retention(env):
    # ADR-0032 dec 4: recoverable for one retention period, then gone — a deleted tenant's history
    # does not outlive the backup's retention (ADR-0033 dec 4).
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    _put(env, COLD, "industryflow-cold", LATE, b"PAR1-late")
    assert _mirror(env, MIRROR_NOW=str(T0)).returncode == 0
    (_store(env, COLD) / "industryflow-cold" / PART).unlink()

    r = _mirror(env, MIRROR_NOW=str(T0 + DAY), MIRROR_RETAIN_DAYS="14")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mirrored(env, PART).exists() and _absent(env) == {PART: str(T0 + DAY)}

    r = _mirror(env, MIRROR_NOW=str(T0 + DAY + 14 * DAY - 1), MIRROR_RETAIN_DAYS="14")
    assert r.returncode == 0 and _mirrored(env, PART).exists()

    r = _mirror(env, MIRROR_NOW=str(T0 + DAY + 14 * DAY), MIRROR_RETAIN_DAYS="14")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not _mirrored(env, PART).exists() and _absent(env) == {}
    assert _mirrored(env, LATE).read_bytes() == b"PAR1-late"  # what the source still holds stays


def test_an_object_back_in_the_source_stops_ageing(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    _put(env, COLD, "industryflow-cold", LATE, b"PAR1-late")
    assert _mirror(env, MIRROR_NOW=str(T0)).returncode == 0
    (_store(env, COLD) / "industryflow-cold" / PART).unlink()
    assert _mirror(env, MIRROR_NOW=str(T0 + DAY)).returncode == 0
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    assert _mirror(env, MIRROR_NOW=str(T0 + 30 * DAY)).returncode == 0
    assert _mirrored(env, PART).exists() and _absent(env) == {}


def test_an_empty_source_beside_a_populated_mirror_ages_nothing(env):
    # A wrong bucket or credential lists nothing; ageing on that would empty the backup.
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    assert _mirror(env, MIRROR_NOW=str(T0)).returncode == 0
    (_store(env, COLD) / "industryflow-cold" / PART).unlink()
    r = _mirror(env, MIRROR_NOW=str(T0 + 365 * DAY))
    assert r.returncode == 0 and "ageing skipped" in r.stdout
    assert _mirrored(env, PART).exists()


def test_a_short_copy_fails_the_backup(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    r = _mirror(env, FAKE_S3_TRUNCATE_PUT="1")
    assert r.returncode != 0
    assert "cold mirror FAILED" in r.stdout


def test_the_backup_run_mirrors_the_cold_bucket(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    e = dict(env["env"], COLD_SOURCE_BUCKET="industryflow-cold", SOURCE_ENDPOINT_URL=COLD, DATABASES="industryflow")
    r = subprocess.run(["bash", str(SCRIPTS / "db-backup.sh")], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mirrored(env, PART).exists()
    assert list((_store(env, env["env"]["S3_ENDPOINT_URL"]) / "backups" / "timescaledb" / "industryflow").glob("*.dump"))


# --- the model-artifact mirror (ADR-0038 dec 1-3) ---------------------------------------------------

ART = "http://art-store:9000"
MODEL = "tenant_x/7/abc/artifacts/model/model.skops"
UPLOAD = "tenant_x/uploads/u1/model.skops"
STAGED = "_upload-staging/tenant_x/u2/model.skops"


def _model_mirror(env, **extra):
    e = dict(env["env"], MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=ART,
             SOURCE_ACCESS_KEY_ID="backup-reader", SOURCE_SECRET_ACCESS_KEY="backup-read-secret", **extra)
    return subprocess.run(["bash", "-c", f'source {SCRIPTS / "db-backup.sh"}; mirror_model_bucket'],
                          env=e, capture_output=True, text=True)


def _model_mirrored(env, key):
    return _store(env, env["env"]["S3_ENDPOINT_URL"]) / "backups" / "models" / key


def test_models_and_admitted_uploads_are_mirrored_but_not_the_staging_area(env):
    for key in (MODEL, UPLOAD, STAGED):
        _put(env, ART, "mlflow", key, b"skops")
    r = _model_mirror(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _model_mirrored(env, MODEL).read_bytes() == b"skops"
    assert _model_mirrored(env, UPLOAD).exists()
    assert not _model_mirrored(env, STAGED).exists()
    calls = _calls(env)
    assert {c["key_id"] for c in calls if c["endpoint"] == "art-store_9000"} == {"backup-reader"}
    assert {c["key_id"] for c in calls if c["endpoint"] == "backup-store_9000"} == {"backup-writer"}


def test_a_deleted_model_leaves_the_mirror_after_one_retention(env):
    _put(env, ART, "mlflow", MODEL, b"skops")
    _put(env, ART, "mlflow", UPLOAD, b"skops")
    assert _model_mirror(env, MIRROR_NOW=str(T0)).returncode == 0
    (_store(env, ART) / "mlflow" / MODEL).unlink()
    assert _model_mirror(env, MIRROR_NOW=str(T0 + DAY)).returncode == 0
    assert _model_mirrored(env, MODEL).exists()
    r = _model_mirror(env, MIRROR_NOW=str(T0 + 15 * DAY), MIRROR_RETAIN_DAYS="14")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not _model_mirrored(env, MODEL).exists() and _model_mirrored(env, UPLOAD).exists()


def test_the_backup_run_mirrors_models_and_cold_into_separate_prefixes(env):
    _put(env, COLD, "industryflow-cold", PART, b"PAR1-day")
    _put(env, COLD, "mlflow", MODEL, b"skops")
    e = dict(env["env"], COLD_SOURCE_BUCKET="industryflow-cold", MODEL_SOURCE_BUCKET="mlflow",
             SOURCE_ENDPOINT_URL=COLD, DATABASES="industryflow")
    r = subprocess.run(["bash", str(SCRIPTS / "db-backup.sh")], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _mirrored(env, PART).exists() and _model_mirrored(env, MODEL).exists()
    assert not _mirrored(env, MODEL).exists()


@pytest.mark.parametrize("bad", ["tenant_x/a\tb", "tenant_x/a\nb"])
def test_a_key_with_a_control_character_fails_the_run_but_not_the_other_objects(env, bad):
    # A tab or newline in a key used to split the text listing into another key: the copy failed and
    # the job aborted, so no tenant's models were mirrored and nothing aged out.
    _put(env, COLD, "mlflow", MODEL, b"skops")
    _put(env, COLD, "mlflow", bad, b"odd")
    e = dict(env["env"], MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=COLD, DATABASES="industryflow")
    r = subprocess.run(["bash", str(SCRIPTS / "db-backup.sh")], env=e, capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert _model_mirrored(env, MODEL).read_bytes() == b"skops"
    assert json.dumps(bad) in r.stdout and "INCOMPLETE: 1 object(s) not mirrored" in r.stdout
    assert not (_store(env, env["env"]["S3_ENDPOINT_URL"]) / "backups" / "models" / "tenant_x" / "a").exists()
