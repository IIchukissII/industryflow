# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No application role creates objects in public (29-user-table.sql).

The owner's SECURITY DEFINER functions and the migration runner resolve unqualified names through
public. A role able to create there could define a better-matching overload of a function they call
(here public.format(text, text) against pg_catalog.format(text, VARIADIC "any")) and have it run as
the owner (CVE-2018-1058). Each attempt runs as the role itself (SET ROLE).

Requires a running database with the scripts applied (db-tenant-isolation.yml stands one up); skips
when none is reachable.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

ROLES = ["api_gateway_user", "spark_streaming_user", "alert_service_user", "ml_service_user",
         "cold_export_user", "tenant_delete_user", "notebook_hub_reclaim_user", "metrics_user"]


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


@pytest.fixture(scope="module")
def cur():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    c = conn.cursor()
    c.execute("SELECT to_regclass('public.\"user\"')")
    if c.fetchone()[0] is None:
        pytest.skip("public.\"user\" not present — scripts not applied")
    yield c
    conn.close()


@pytest.mark.parametrize("role", ROLES)
def test_no_application_role_may_create_in_public(cur, role):
    cur.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE')", (role,))
    assert cur.fetchone()[0] is False


def test_the_gateway_cannot_plant_an_overload_the_owner_would_call(cur):
    cur.execute('SET ROLE "api_gateway_user"')
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute("CREATE FUNCTION public.format(text, text) RETURNS text "
                        "LANGUAGE sql AS $$ SELECT 'planted' $$")
    finally:
        cur.execute("RESET ROLE")
    cur.execute("SELECT count(*) FROM pg_proc WHERE proname = 'format' "
                "AND pronamespace = 'public'::regnamespace")
    assert cur.fetchone()[0] == 0


def test_the_user_table_is_the_owners_and_the_gateway_keeps_its_rows(cur):
    cur.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.\"user\"'::regclass")
    assert cur.fetchone()[0] != "api_gateway_user"
    uid, cid = str(uuid.uuid4()), str(uuid.uuid4())
    cur.execute('SET ROLE "api_gateway_user"')
    try:
        cur.execute('INSERT INTO public."user" (id, email, hashed_password, is_active, is_superuser, '
                    "is_verified, company_id, role) VALUES (%s, %s, 'x', true, false, false, %s, 'observer')",
                    (uid, f"{uid}@example.invalid", cid))
        cur.execute('UPDATE public."user" SET is_verified = true WHERE id = %s', (uid,))
        cur.execute('SELECT is_verified FROM public."user" WHERE id = %s', (uid,))
        assert cur.fetchone()[0] is True
        cur.execute('DELETE FROM public."user" WHERE id = %s', (uid,))
    finally:
        cur.execute("RESET ROLE")


@pytest.mark.parametrize("role", ["alert_service_user", "ml_service_user"])
def test_the_readers_still_read_the_user_table(cur, role):
    cur.execute('SELECT has_table_privilege(%s, \'public."user"\', \'SELECT\')', (role,))
    assert cur.fetchone()[0] is True
