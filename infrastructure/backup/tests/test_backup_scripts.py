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


# A data-only dump as pg_restore -a / pg_dump -a print it: one COPY block per table.
def _copy(table, *rows):
    return f"COPY {table} (a, b) FROM stdin;\n" + "".join(f"{r}\n" for r in rows) + "\\.\n\n"


DUMP_DATA = ("--\n-- Data for Name: companies\n--\n\n" + _copy("public.companies", "1\ta", "2\tb")
             + _copy('public."user"', "1\tx") + "SELECT pg_catalog.setval('s', 2, true);\n")


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
    _exe(bin_dir / "psql", f"#!{sys.executable}\nimport runpy, sys\n"
                           f"sys.argv[0] = 'psql'\nrunpy.run_path({str(HERE / 'fake_psql.py')!r}, run_name='__main__')\n")
    # -l prints the table of contents; -f writes the script (-L: the database-level one); otherwise
    # it is the restore itself.
    toc = tmp_path / "toc.list"
    toc.write_text(TOC)
    _exe(bin_dir / "pg_restore", f'#!/usr/bin/env bash\necho "$*" >> {tmp_path / "pg_restore.log"}\n'
         'out=; list=; prev=\n'
         'for a; do case "$a" in -l) cat "${FAKE_PG_RESTORE_TOC:-' + str(toc) + '}"; exit 0 ;; esac\n'
         '  [ "$prev" = -f ] && out="$a"; [ "$prev" = -L ] && list="$a"; prev="$a"; done\n'
         'if [ "$out" = - ]; then [ -z "$FAKE_PG_RESTORE_DATA_FAIL" ] || exit 1\n'
         '  printf "%s" "${FAKE_DUMP_DATA-$DEFAULT_DUMP_DATA}"; exit 0; fi\n'
         'if [ -n "$out" ]; then\n'
         '  if [ -n "$list" ]; then printf "%s" "${FAKE_PG_RESTORE_DB_SQL:-}" > "$out"\n'
         '  else printf "%s" "${FAKE_PG_RESTORE_SQL:-}" > "$out"; fi\n'
         '  exit 0\nfi\n'
         'cat "$FAKE_PG_RESTORE_STDERR" >&2\nexit "${FAKE_PG_RESTORE_RC:-0}"\n')
    # -a (the rehearsal reading its restored copy back) prints that copy's data; otherwise a dump file.
    _exe(bin_dir / "pg_dump", f'#!/usr/bin/env bash\necho "$*" >> {tmp_path / "pg_dump.log"}\n'
                              '[ -z "$FAKE_PG_DUMP_FAIL" ] || exit 1\n'
                              'if [ "$1" = -a ]; then printf "%s" "${FAKE_RESTORED_DATA-${FAKE_DUMP_DATA-$DEFAULT_DUMP_DATA}}"; exit 0; fi\n'
                              'while [ "$1" != "-f" ]; do shift; done; echo dump > "$2"\n')
    _exe(bin_dir / "pg_dumpall", "#!/usr/bin/env bash\necho '-- globals'\n")
    e = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), "FAKE_S3_ROOT": str(s3),
        "FAKE_PSQL_LOG": str(calls), "DEFAULT_DUMP_DATA": DUMP_DATA,
        "PGHOST": "db", "PGUSER": "postgres", "PGPASSWORD": "pw", "S3_BUCKET": "backups",
        "S3_ENDPOINT_URL": "http://backup-store:9000",
        "AWS_ACCESS_KEY_ID": "backup-writer", "AWS_SECRET_ACCESS_KEY": "backup-secret",
    }
    return {"env": e, "s3": s3, "psql": calls, "tmp": tmp_path}


def _store(env, endpoint):
    return env["s3"] / endpoint.split("://", 1)[-1].replace(":", "_").replace("/", "_")


# Objects are written before the backup run of STAMP unless a test says otherwise.
BEFORE_THE_RUN = 1767225600  # 2026-01-01T00:00:00Z


def _put(env, endpoint, bucket, key, data: bytes, modified: int = BEFORE_THE_RUN):
    path = _store(env, endpoint) / bucket / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (modified, modified))


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


# --- db-backup.sh: the dump and its success time ----------------------------------------------------

BACKUPS = "http://backup-store:9000"


def _backup_run(env, **extra):
    e = {**env["env"], "DATABASES": "industryflow", **extra}
    return subprocess.run(["bash", str(SCRIPTS / "db-backup.sh")], env=e, capture_output=True, text=True)


def _keys(env, prefix):
    root = _store(env, BACKUPS) / "backups"
    return sorted(str(p.relative_to(root)) for p in (root / prefix).rglob("*") if p.is_file()) \
        if (root / prefix).exists() else []


def test_the_backup_is_one_plain_dump_per_database(env):
    # The rehearsal counts what a dump holds from the dump itself: the backup holds no session or
    # lock of its own on the database beside pg_dump's.
    r = _backup_run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_keys(env, "timescaledb/industryflow")) == 1
    [dump] = (env["tmp"] / "pg_dump.log").read_text().splitlines()
    assert dump.startswith("-Fc -Z6 ") and "--snapshot" not in dump
    # The other sessions are the backup ledger's: made sure of before the dumps, so every dump holds
    # it, and the run's record after them (ADR-0032 dec 7).
    ensure, record = env["psql"].read_text().splitlines()
    assert "-d industryflow" in ensure and "-v run=" not in ensure
    assert "-d industryflow" in record and "-v run=" in record


def _recorded_run(env, **extra):
    stdin = env["tmp"] / "psql.stdin"
    r = _backup_run(env, FAKE_PSQL_STDIN_LOG=str(stdin), **extra)
    return r, (stdin.read_text() if stdin.exists() else "")


def test_a_complete_run_records_itself_in_the_platform_database(env):
    r, script = _recorded_run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "INSERT INTO public.backup_ledger (run, databases) VALUES (:'run', :'databases')" in script
    stamp = _keys(env, "timescaledb/industryflow")[0].rsplit("/", 1)[1].removesuffix(".dump")
    assert f"-v run={stamp}" in env["psql"].read_text()
    assert "run recorded in industryflow.public.backup_ledger" in r.stdout


def test_the_ledger_exists_before_the_dumps_and_a_failed_run_records_nothing(env):
    r, script = _recorded_run(env, FAKE_PG_DUMP_FAIL="1")
    assert r.returncode != 0
    assert "CREATE TABLE IF NOT EXISTS public.backup_ledger" in script
    assert "INSERT INTO public.backup_ledger" not in script


def test_a_run_without_the_platform_database_records_nothing(env):
    r, script = _recorded_run(env, DATABASES="mlflow")
    assert r.returncode == 0, r.stdout + r.stderr
    assert script == "" and not env["psql"].exists()
    assert "industryflow not among the dumped databases" in r.stdout


def test_a_run_that_cannot_record_itself_fails_without_its_metric(env):
    # The migration runner would not see the backup; the run is not a success.
    metrics = env["tmp"] / "metrics"
    metrics.mkdir()
    r, _ = _recorded_run(env, FAKE_PSQL_STDIN_FAIL="1", METRICS_TEXTFILE_DIR=str(metrics))
    assert r.returncode != 0
    assert list(metrics.iterdir()) == []


def test_a_successful_backup_writes_its_metric_and_a_failed_one_does_not(env):
    metrics = env["tmp"] / "metrics"
    metrics.mkdir()
    assert _backup_run(env, METRICS_TEXTFILE_DIR=str(metrics)).returncode == 0
    text = (metrics / "db-backup.prom").read_text()
    assert 'industryflow_backup_last_success_timestamp_seconds{backup="databases"} ' in text
    (metrics / "db-backup.prom").unlink()
    assert _backup_run(env, METRICS_TEXTFILE_DIR=str(metrics), FAKE_PG_DUMP_FAIL="1").returncode != 0
    assert list(metrics.iterdir()) == []


def test_restore_latest_takes_only_a_dump(env):
    _put(env, BACKUPS, "backups", "timescaledb/industryflow/20260101T020000Z.dump", b"x")
    _put(env, BACKUPS, "backups", "timescaledb/industryflow/20260101T020000Z.zzz", b"x")
    (env["tmp"] / "restore.err").write_text("")
    e = dict(env["env"], FAKE_PG_RESTORE_STDERR=str(env["tmp"] / "restore.err"))
    r = subprocess.run(["bash", str(SCRIPTS / "db-restore.sh"), "industryflow", "latest", "--target", "t"],
                       env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "20260101T020000Z.dump -> database 't'" in r.stdout


@pytest.mark.parametrize("hold", [True, False])
def test_hold_jobs_unschedules_the_restored_policies_before_the_scheduler_starts(env, hold):
    r = _restore(env, "", 0) if not hold else None
    if hold:
        (env["tmp"] / "restore.err").write_text("")
        _put(env, BACKUPS, "backups", "timescaledb/industryflow/20260101T020000Z.dump", b"x")
        e = dict(env["env"], FAKE_PG_RESTORE_STDERR=str(env["tmp"] / "restore.err"))
        r = subprocess.run(["bash", str(SCRIPTS / "db-restore.sh"), "industryflow", "latest",
                            "--target", "t", "--hold-jobs"], env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    log = env["psql"].read_text().splitlines()
    held = [i for i, c in enumerate(log) if "_timescaledb_config.bgw_job SET scheduled = false" in c]
    post = [i for i, c in enumerate(log) if "timescaledb_post_restore()" in c]
    assert bool(held) == hold
    if hold:
        assert held[0] < post[0]


# --- db-rehearse.sh (ADR-0032 dec 6) ---------------------------------------------------------------

STAMP = "20260101T020000Z"


def _backed_up(env, db="industryflow"):
    _put(env, BACKUPS, "backups", f"timescaledb/{db}/{STAMP}.dump", b"x")


def _rehearse(env, **extra):
    (env["tmp"] / "restore.err").write_text("")
    e = dict(env["env"], DATABASES="industryflow", FAKE_PG_RESTORE_STDERR=str(env["tmp"] / "restore.err"),
             PATH=f"{SCRIPTS}:{env['env']['PATH']}", **extra)
    return subprocess.run(["bash", str(SCRIPTS / "db-rehearse.sh")], env=e, capture_output=True, text=True)


def test_a_rehearsal_restores_the_latest_dump_into_scratch_compares_and_drops_it(env):
    _backed_up(env)
    metrics = env["tmp"] / "metrics"
    metrics.mkdir()
    r = _rehearse(env, METRICS_TEXTFILE_DIR=str(metrics))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 tables match the rows the backup holds" in r.stdout
    log = env["psql"].read_text()
    assert 'CREATE DATABASE "industryflow_rehearsal"' in log
    assert 'DROP DATABASE IF EXISTS "industryflow_rehearsal" WITH (FORCE);' in log.splitlines()[-1]
    # The scratch copy runs no restored policy.
    assert "_timescaledb_config.bgw_job SET scheduled = false" in log
    text = (metrics / "db-rehearse.prom").read_text()
    assert 'industryflow_restore_rehearsal_last_success_timestamp_seconds{backup="databases"} ' in text
    assert 'industryflow_restore_rehearsal_duration_seconds{database="industryflow"} ' in text


@pytest.mark.parametrize("restored, why", [
    (_copy("public.companies", "1\ta") + _copy('public."user"', "1\tx"),
     "differs: public.companies (2 dumped, 1 restored)"),
    (_copy('public."user"', "1\tx"), "missing: public.companies (2 rows dumped)"),
    (DUMP_DATA + _copy("public.x", "9\tz"), "not in the backup: public.x (1 rows)"),
    (DUMP_DATA + _copy("_timescaledb_internal._hyper_1_1_chunk", "1\tq"),
     "not in the backup: _timescaledb_internal._hyper_1_1_chunk (1 rows)"),
])
def test_a_restore_that_differs_from_the_backup_fails_and_still_drops_scratch(env, restored, why):
    _backed_up(env)
    metrics = env["tmp"] / "metrics"
    metrics.mkdir()
    r = _rehearse(env, FAKE_RESTORED_DATA=restored, METRICS_TEXTFILE_DIR=str(metrics))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "the restored rows differ from the backup's" in r.stdout and why in r.stdout
    assert 'DROP DATABASE IF EXISTS "industryflow_rehearsal"' in env["psql"].read_text()
    assert list(metrics.iterdir()) == []


def test_empty_tables_the_dump_lacks_and_timescaledb_s_scheduler_tables_are_not_compared(env):
    _backed_up(env)
    dumped = (DUMP_DATA + _copy("_timescaledb_catalog.chunk", "1\tc") + _copy("_timescaledb_catalog.metadata", "u")
              + _copy("_timescaledb_config.bgw_job", "1000\tj"))
    restored = (DUMP_DATA + _copy("public.empty_parent") + _copy("_timescaledb_catalog.chunk", "1\tc")
                + _copy("_timescaledb_catalog.metadata", "u", "v") + _copy("_timescaledb_internal.bgw_job_stat", "1\ts"))
    r = _rehearse(env, FAKE_DUMP_DATA=dumped, FAKE_RESTORED_DATA=restored)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 tables match the rows the backup holds" in r.stdout


def test_a_restore_that_lost_chunk_metadata_fails(env):
    _backed_up(env)
    dumped = DUMP_DATA + _copy("_timescaledb_catalog.dimension_slice", "1\ts", "2\ts")
    restored = DUMP_DATA + _copy("_timescaledb_catalog.dimension_slice", "1\ts")
    r = _rehearse(env, FAKE_DUMP_DATA=dumped, FAKE_RESTORED_DATA=restored)
    assert r.returncode == 1
    assert "differs: _timescaledb_catalog.dimension_slice (2 dumped, 1 restored)" in r.stdout


def test_a_target_that_cannot_be_listed_fails_with_a_line(env):
    r = _rehearse(env, FAKE_S3_FAIL_LIST="1")
    assert r.returncode == 1
    assert "the backup target could not be listed" in r.stdout


def test_a_dump_that_cannot_be_read_fails_the_rehearsal_before_restoring(env):
    _backed_up(env)
    r = _rehearse(env, FAKE_PG_RESTORE_DATA_FAIL="1")
    assert r.returncode == 1
    assert "could not be read" in r.stdout
    assert "CREATE DATABASE" not in (env["psql"].read_text() if env["psql"].exists() else "")


def test_a_dump_holding_no_table_is_rehearsed_and_says_so(env):
    _backed_up(env)
    r = _rehearse(env, FAKE_DUMP_DATA="", FAKE_RESTORED_DATA="")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "holds no table data" in r.stdout and 'CREATE DATABASE "industryflow_rehearsal"' in env["psql"].read_text()
    # Rows the empty backup never held still fail.
    r = _rehearse(env, FAKE_DUMP_DATA="", FAKE_RESTORED_DATA=_copy("public.x", "1"))
    assert r.returncode == 1 and "not in the backup: public.x (1 rows)" in r.stdout


def test_no_backup_fails_the_rehearsal(env):
    r = _rehearse(env)
    assert r.returncode == 1
    assert "no backup to rehearse" in r.stdout


def test_a_failed_restore_fails_the_rehearsal(env):
    _backed_up(env)
    (env["tmp"] / "other.err").write_text(OTHER)
    # pg_restore fails with an error that is not the expected extension collision.
    r = subprocess.run(["bash", str(SCRIPTS / "db-rehearse.sh")], capture_output=True, text=True,
                       env=dict(env["env"], DATABASES="industryflow", FAKE_PG_RESTORE_RC="1",
                                FAKE_PG_RESTORE_STDERR=str(env["tmp"] / "other.err"),
                                PATH=f"{SCRIPTS}:{env['env']['PATH']}"))
    assert r.returncode == 1
    assert "the restore did not complete" in r.stdout
    assert 'DROP DATABASE IF EXISTS "industryflow_rehearsal"' in env["psql"].read_text()


def test_an_empty_suffix_is_refused_before_anything_is_restored(env):
    _backed_up(env)
    r = _rehearse(env, REHEARSAL_SUFFIX="")
    assert r.returncode == 2
    assert "CREATE DATABASE" not in (env["psql"].read_text() if env["psql"].exists() else "")


def _mirrored_pair(env, key, source: bytes, mirror: bytes, prefix="models", bucket="mlflow"):
    _put(env, ART, bucket, key, source)
    _put(env, BACKUPS, "backups", f"{prefix}/{key}", mirror)


def test_mirrored_objects_are_read_back_and_compared(env):
    _backed_up(env)
    _mirrored_pair(env, MODEL, b"skops", b"skops")
    r = _rehearse(env, MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=ART,
                  SOURCE_ACCESS_KEY_ID="backup-reader", SOURCE_SECRET_ACCESS_KEY="s")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "model mirror: 1 sampled object(s) read back and compared" in r.stdout


def test_a_mirrored_object_with_other_bytes_fails_the_rehearsal(env):
    _backed_up(env)
    _mirrored_pair(env, MODEL, b"skops", b"skopz")  # same size, other bytes
    r = _rehearse(env, MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=ART)
    assert r.returncode == 1
    assert f"{MODEL} differs between the source and the mirror" in r.stdout


def test_a_mirror_holding_none_of_the_source_fails_the_rehearsal(env):
    _backed_up(env)
    _put(env, ART, "mlflow", MODEL, b"skops")
    r = _rehearse(env, MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=ART)
    assert r.returncode == 1
    assert "the mirror holds none of them" in r.stdout


def test_every_source_object_missing_from_the_mirror_fails_the_rehearsal_named(env):
    """Object for object (ADR-0038 dec 8): a mirror holding some of the source is not the source."""
    _backed_up(env)
    _mirrored_pair(env, MODEL, b"skops", b"skops")
    lost = MODEL.replace("model.skops", "MLmodel")
    _put(env, ART, "mlflow", lost, b"flavors")
    r = _rehearse(env, MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=ART)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"model mirror: FAILED, {lost} is missing from the mirror" in r.stdout
    assert MODEL + " " not in r.stdout  # the object the mirror holds is not reported


def test_an_object_written_after_the_rehearsed_run_is_not_required_in_the_mirror(env):
    """The run of the rehearsed dump could not copy what the source gained after it started."""
    _backed_up(env)
    _mirrored_pair(env, MODEL, b"skops", b"skops")
    later = MODEL.replace("model.skops", "MLmodel")
    _put(env, ART, "mlflow", later, b"flavors", modified=1767232800)  # 2026-01-01T02:00:00Z, the run
    r = _rehearse(env, MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=ART)
    assert r.returncode == 0, r.stdout + r.stderr
    assert later not in r.stdout


def test_a_rewritten_cold_manifest_is_not_sampled(env):
    _backed_up(env)
    _mirrored_pair(env, "tenant_x/2026/01/01/_manifest.json", b"{new}", b"{old}", prefix="cold",
                   bucket="industryflow-cold")
    _mirrored_pair(env, PART, b"PAR1", b"PAR1", prefix="cold", bucket="industryflow-cold")
    r = _rehearse(env, COLD_SOURCE_BUCKET="industryflow-cold", SOURCE_ENDPOINT_URL=ART)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "cold mirror: 1 sampled object(s)" in r.stdout


# --- backup-lib.sh ---------------------------------------------------------------------------------

def test_write_metric_replaces_the_file_whole_and_is_a_no_op_without_a_directory(env):
    d = env["tmp"] / "m"
    d.mkdir()
    run = lambda e: subprocess.run(  # noqa: E731
        ["bash", "-c", f'source {SCRIPTS / "backup-lib.sh"}; write_metric x "a 1" "b 2"'],
        env=e, capture_output=True, text=True)
    assert run(dict(env["env"], METRICS_TEXTFILE_DIR=str(d))).returncode == 0
    assert (d / "x.prom").read_text() == "a 1\nb 2\n"
    assert oct((d / "x.prom").stat().st_mode & 0o777) == "0o644"
    assert [p.name for p in d.iterdir()] == ["x.prom"]
    assert run(env["env"]).returncode == 0


def test_restore_latest_with_no_dump_says_so(env):
    _put(env, BACKUPS, "backups", "timescaledb/industryflow/20260101T020000Z.zzz", b"x")
    r = subprocess.run(["bash", str(SCRIPTS / "db-restore.sh"), "industryflow", "latest", "--target", "t"],
                       env=env["env"], capture_output=True, text=True)
    assert r.returncode == 1
    assert "no backups for industryflow" in r.stderr


def test_an_unwritable_metric_directory_warns_without_failing_the_job(env):
    d = env["tmp"] / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        r = _backup_run(env, METRICS_TEXTFILE_DIR=str(d))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "could not write the db-backup metric" in r.stderr
    finally:
        d.chmod(0o700)


def test_a_mirrored_object_of_another_size_fails_the_rehearsal(env):
    _backed_up(env)
    _mirrored_pair(env, MODEL, b"skops", b"sko")  # truncated in the mirror
    _mirrored_pair(env, UPLOAD, b"skops", b"skops")
    r = _rehearse(env, MODEL_SOURCE_BUCKET="mlflow", SOURCE_ENDPOINT_URL=ART)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"model mirror: FAILED, {MODEL} differs in size between the source and the mirror" in r.stdout


def test_a_rewritten_manifest_of_another_size_is_not_a_size_failure(env):
    _backed_up(env)
    _mirrored_pair(env, "tenant_x/2026/01/01/_manifest.json", b"{newer}", b"{old}", prefix="cold",
                   bucket="industryflow-cold")
    _mirrored_pair(env, PART, b"PAR1", b"PAR1", prefix="cold", bucket="industryflow-cold")
    r = _rehearse(env, COLD_SOURCE_BUCKET="industryflow-cold", SOURCE_ENDPOINT_URL=ART)
    assert r.returncode == 0, r.stdout + r.stderr


def test_copy_rows_counts_each_line_of_a_copy_block_as_one_row(env):
    data = (_copy("public.a", "1\\nan escaped newline", "2") + _copy('"Odd Schema"."t ("') + "SELECT 1;\n")
    r = subprocess.run(["bash", "-c", f'source {SCRIPTS / "backup-lib.sh"}; copy_rows'], input=data,
                       env=env["env"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["public.a\t2", '"Odd Schema"."t ("\t0']


def test_the_hook_of_the_upgrade_enabling_backups_skips_without_credentials(env):
    e = {k: v for k, v in env["env"].items() if k != "PGUSER"}
    r = subprocess.run(["bash", str(SCRIPTS / "db-backup.sh")], env={**e, "SKIP_WITHOUT_CREDENTIALS": "1"},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no backup credentials yet" in r.stdout
    assert not (env["tmp"] / "pg_dump.log").exists()
    # Without the flag a missing credential is a failed run, as before.
    r = subprocess.run(["bash", str(SCRIPTS / "db-backup.sh")], env=e, capture_output=True, text=True)
    assert r.returncode != 0
