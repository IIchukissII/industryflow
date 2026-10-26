# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A real backup -> restore keeps every owner and privilege (infrastructure/backup).

The backup image's own db-backup.sh dumps the live databases to the compose MinIO; its db-restore.sh
restores them into scratch databases on the same cluster. The restored databases must then match
their sources object for object — owners, table/column/sequence/function/schema ACLs, default
privileges, the database's own ACL — and the service and tenant roles must still be able to do what
they did before. A restore that drops the grants leaves every service role, and every tenant's
reader role, with no access to the data it was restored for.

A dump that references a role the cluster lacks must be refused before the target is touched.

Needs the compose stack (TimescaleDB with the init scripts, MinIO, the db-backup image) and docker on
PATH; db-tenant-isolation.yml provides them. Skips otherwise; with IF_REQUIRE_LIVE_STACK set a skip
is a failure.
"""
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

REPO = Path(__file__).resolve().parents[3]
SOURCES = ("industryflow", "mlflow")
RESTORED = {db: f"{db}_restore_privileges_test" for db in SOURCES}
REFUSED = "industryflow_restore_refused_test"
PROBE_ROLE = "restore_probe_missing_role"
GATEWAY_TABLE = "restore_probe_gateway_owned"


def _skip(reason):
    if os.getenv("IF_REQUIRE_LIVE_STACK"):
        pytest.fail(f"required restore proof could not run: {reason}")
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


def _compose(*args):
    """Runs the db-backup service of the compose stack; returns the completed process."""
    return subprocess.run(
        ["docker", "compose", "run", "--rm", "-T", "-e", "COLD_SOURCE_BUCKET=", *args],
        cwd=REPO, capture_output=True, text=True, timeout=600,
    )


def _backup():
    r = _compose("db-backup")
    assert r.returncode == 0, f"backup failed:\n{r.stdout}\n{r.stderr}"
    time.sleep(1.1)  # backup keys are stamped to the second; the next run must sort after this one


def _restore(db, target):
    return _compose("--entrypoint", "db-restore.sh", "db-backup", db, "latest", "--target", target)


def _drop_database(cur, name):
    cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# Every owner and ACL a restore could lose, keyed by object. ACLs are compared as sets with NULL
# read as the type's default, so only a real difference in who may do what counts.
SNAPSHOT = """
SET search_path = pg_catalog;
WITH ns AS (
    SELECT oid, nspname FROM pg_namespace
    WHERE nspname NOT IN ('pg_catalog', 'information_schema')
      AND nspname NOT LIKE 'pg\\_toast%' AND nspname NOT LIKE 'pg\\_temp%'
)
SELECT 'schema', n.nspname, pg_get_userbyid(n.nspowner),
       ARRAY(SELECT unnest(coalesce(n.nspacl, acldefault('n'::"char", n.nspowner)))::text ORDER BY 1)
  FROM pg_namespace n JOIN ns USING (oid)
UNION ALL
SELECT 'relation', ns.nspname || '.' || c.relname, pg_get_userbyid(c.relowner),
       ARRAY(SELECT unnest(coalesce(c.relacl,
             acldefault(CASE c.relkind WHEN 'S' THEN 's' ELSE 'r' END::"char", c.relowner)))::text ORDER BY 1)
  FROM pg_class c JOIN ns ON ns.oid = c.relnamespace
UNION ALL
SELECT 'column', ns.nspname || '.' || c.relname || '.' || a.attname, NULL,
       ARRAY(SELECT unnest(a.attacl)::text ORDER BY 1)
  FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN ns ON ns.oid = c.relnamespace
 WHERE a.attacl IS NOT NULL
UNION ALL
SELECT 'function', p.oid::regprocedure::text, pg_get_userbyid(p.proowner),
       ARRAY(SELECT unnest(coalesce(p.proacl, acldefault('f'::"char", p.proowner)))::text ORDER BY 1)
  FROM pg_proc p JOIN ns ON ns.oid = p.pronamespace
UNION ALL
SELECT 'type', ns.nspname || '.' || t.typname, pg_get_userbyid(t.typowner),
       ARRAY(SELECT unnest(coalesce(t.typacl, acldefault('T'::"char", t.typowner)))::text ORDER BY 1)
  FROM pg_type t JOIN ns ON ns.oid = t.typnamespace
UNION ALL
SELECT 'default acl',
       pg_get_userbyid(d.defaclrole) || ' in ' || coalesce(n.nspname, '*') || ' on ' || d.defaclobjtype::text,
       NULL, ARRAY(SELECT unnest(d.defaclacl)::text ORDER BY 1)
  FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
UNION ALL
SELECT 'database', '(this database)', pg_get_userbyid(datdba),
       ARRAY(SELECT unnest(coalesce(datacl, acldefault('d'::"char", datdba)))::text ORDER BY 1)
  FROM pg_database WHERE datname = current_database()
"""


def _snapshot(dbname):
    conn = _connect(dbname)
    try:
        cur = conn.cursor()
        cur.execute(SNAPSHOT)
        return {(kind, name): (owner, tuple(acl)) for kind, name, owner, acl in cur.fetchall()}
    finally:
        conn.close()


@pytest.fixture(scope="module")
def stack():
    if shutil.which("docker") is None:
        _skip("docker is not on PATH")
    try:
        admin = _connect("postgres")
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        _skip(f"no database reachable: {e}")
    acur = admin.cursor()
    src = _connect("industryflow")
    cur = src.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'provision_company'")
    if cur.fetchone() is None:
        _skip("provision_company() not present — init scripts not applied")

    # A tenant as the gateway provisions one, with a measurement so its hypertable has a chunk.
    cur.execute('SET ROLE "api_gateway_user"')
    cur.execute("SELECT company_id FROM public.provision_company(%s, TRUE)", (f"restore-{uuid.uuid4()}",))
    company_id = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    suffix = str(company_id).replace("-", "_")
    schema, reader = f"tenant_{suffix}", f"tenant_reader_{suffix}"
    cur.execute(f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
                f"VALUES ('pump', 'p1', 1) RETURNING equipment_id")
    equipment_id = cur.fetchone()[0]
    cur.execute(f'INSERT INTO "{schema}".sensors (equipment_id, sensor_name, sensor_type, position) '
                f"VALUES (%s, 's1', 'temperature', 1) RETURNING sensor_id", (equipment_id,))
    sensor_id = cur.fetchone()[0]
    cur.execute(f'INSERT INTO "{schema}".sensor_measurements (time, sensor_id, equipment_id, site_id, value) '
                f"VALUES (now(), %s, %s, 'site', 1.0)", (sensor_id, equipment_id))
    # A table owned by a non-superuser, as public."user" was before 29-user-table.sql: its owner must
    # survive. (No application role may create in public, so the superuser hands it over.)
    cur.execute(f"CREATE TABLE public.{GATEWAY_TABLE} (id INT)")
    cur.execute(f'ALTER TABLE public.{GATEWAY_TABLE} OWNER TO "api_gateway_user"')

    for target in (*RESTORED.values(), REFUSED):
        _drop_database(acur, target)

    _backup()
    restores = {db: _restore(db, target) for db, target in RESTORED.items()}

    # A second backup that references a role the cluster then loses, restored over a target that
    # already holds data: the restore must refuse, naming the role, and leave the target as it was.
    cur.execute(f'CREATE ROLE "{PROBE_ROLE}" NOLOGIN')
    cur.execute(f'GRANT SELECT ON public.{GATEWAY_TABLE} TO "{PROBE_ROLE}"')
    _backup()
    cur.execute(f'REVOKE ALL ON public.{GATEWAY_TABLE} FROM "{PROBE_ROLE}"')
    cur.execute(f'DROP ROLE "{PROBE_ROLE}"')
    acur.execute(f'CREATE DATABASE "{REFUSED}"')
    with _connect(REFUSED) as kept:
        kept.cursor().execute("CREATE TABLE kept_marker (id INT)")
    kept.close()
    refused = _restore("industryflow", REFUSED)

    yield {"restores": restores, "refused": refused, "schema": schema, "reader": reader}

    for target in (*RESTORED.values(), REFUSED):
        _drop_database(acur, target)
    cur.execute(f"DROP TABLE IF EXISTS public.{GATEWAY_TABLE}")
    cur.execute(f'DROP ROLE IF EXISTS "{PROBE_ROLE}"')
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
    cur.execute(f'DROP ROLE IF EXISTS "{reader}"')
    cur.execute("DELETE FROM public.companies WHERE company_id = %s", (str(company_id),))
    src.close()
    admin.close()


@pytest.mark.parametrize("db", SOURCES)
def test_the_restore_succeeds(stack, db):
    r = stack["restores"][db]
    assert r.returncode == 0, f"restore of {db} failed:\n{r.stdout}\n{r.stderr}"


@pytest.mark.parametrize("db", SOURCES)
def test_every_owner_and_privilege_survives_the_restore(stack, db):
    assert stack["restores"][db].returncode == 0
    before, after = _snapshot(db), _snapshot(RESTORED[db])
    missing = sorted(set(before) - set(after))
    extra = sorted(set(after) - set(before))
    differ = {k: (before[k], after[k]) for k in before.keys() & after.keys() if before[k] != after[k]}
    assert not missing, f"objects missing after the restore: {missing[:20]}"
    assert not extra, f"objects the source does not have, after the restore: {extra[:20]}"
    assert not differ, "owners/ACLs changed by the restore:\n" + "\n".join(
        f"  {k}: {v[0]} -> {v[1]}" for k, v in sorted(differ.items())[:40])


def _can(cur, role, sql):
    cur.execute(f'SET ROLE "{role}"')
    try:
        cur.execute(sql)
        return cur.fetchall()
    finally:
        cur.execute("RESET ROLE")


def test_service_and_tenant_roles_keep_their_access(stack):
    assert stack["restores"]["industryflow"].returncode == 0
    schema, reader = stack["schema"], stack["reader"]
    conn = _connect(RESTORED["industryflow"])
    try:
        cur = conn.cursor()
        measurements = f'SELECT count(*) FROM "{schema}".sensor_measurements'
        for role in ("api_gateway_user", "spark_streaming_user", "alert_service_user", "ml_service_user",
                     "cold_export_user", reader):
            assert _can(cur, role, measurements) == [(1,)], role
        assert _can(cur, "api_gateway_user", f'SELECT count(*) FROM "{schema}".sensors') == [(1,)]
        assert _can(cur, "alert_service_user", f'SELECT count(*) FROM "{schema}".ml_models') == [(0,)]
        assert _can(cur, "api_gateway_user", "SELECT count(*) FROM public.companies")[0][0] >= 1
        for role, priv, table in [
            ("api_gateway_user", "INSERT, UPDATE, DELETE", f"{schema}.sensors"),
            ("spark_streaming_user", "INSERT, UPDATE", f"{schema}.sensor_measurements"),
            ("alert_service_user", "INSERT, UPDATE, DELETE", f"{schema}.alerts"),
            ("ml_service_user", "INSERT, UPDATE, DELETE", f"{schema}.ml_models"),
        ]:
            cur.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, priv))
            assert cur.fetchone() == (True,), (role, priv, table)
        for role, fn in [
            ("api_gateway_user", "public.provision_company(text, boolean)"),
            ("cold_export_user",
             "public.cold_export_drop_day(text, timestamptz, timestamptz, bigint, numeric, text)"),
        ]:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
            assert cur.fetchone() == (True,), (role, fn)
        # The restored SECURITY DEFINER provisioning still runs as its owner, for the gateway.
        cur.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = "
                    "'public.provision_company(text, boolean)'::regprocedure")
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", (cur.fetchone()[0],))
        assert cur.fetchone() == (True,)
        # The gateway-owned table keeps its owner.
        cur.execute(f"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.{GATEWAY_TABLE}'::regclass")
        assert cur.fetchone() == ("api_gateway_user",)
    finally:
        conn.close()


def test_default_privileges_still_grant_future_tables(stack):
    assert stack["restores"]["industryflow"].returncode == 0
    schema, reader = stack["schema"], stack["reader"]
    conn = _connect(RESTORED["industryflow"])
    try:
        cur = conn.cursor()
        # A table added to the tenant schema later is readable by the tenant's reader ...
        cur.execute(f'CREATE TABLE "{schema}".added_after_restore (id INT)')
        assert _can(cur, reader, f'SELECT count(*) FROM "{schema}".added_after_restore') == [(0,)]
        # ... and the user table keeps its readers and its writer.
        for role in ("alert_service_user", "ml_service_user"):
            assert _can(cur, role, 'SELECT count(*) FROM public."user"')[0][0] >= 0, role
        cur.execute("""SELECT has_table_privilege('api_gateway_user', 'public."user"', 'INSERT, UPDATE, DELETE')""")
        assert cur.fetchone() == (True,)
    finally:
        conn.close()


def test_the_database_acl_survives_the_restore(stack):
    assert stack["restores"]["mlflow"].returncode == 0
    conn = _connect("postgres")
    try:
        cur = conn.cursor()
        cur.execute("SELECT has_database_privilege('mlflow_user', %s, 'CREATE')", (RESTORED["mlflow"],))
        assert cur.fetchone() == (True,)
    finally:
        conn.close()


def test_a_dump_naming_a_missing_role_is_refused_before_the_target_is_touched(stack):
    r = stack["refused"]
    assert r.returncode != 0, f"restore with a missing role succeeded:\n{r.stdout}\n{r.stderr}"
    assert PROBE_ROLE in r.stdout + r.stderr
    conn = _connect(REFUSED)
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.kept_marker') IS NOT NULL")
        assert cur.fetchone() == (True,), "the refused restore dropped or replaced the target database"
    finally:
        conn.close()
