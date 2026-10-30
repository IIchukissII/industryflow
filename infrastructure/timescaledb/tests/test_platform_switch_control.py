# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Platform switch control (init script 32, ADR-0043), against the real database, as the API gateway's
own role and with its own statements on its driver (service_sql.py):

  * a flip changes the switch and appends one record of it, with its actor, before and after,
  * a flip to the current value changes and records nothing,
  * an unknown switch, or a value of another type, is refused,
  * no runtime role writes the switch or the record directly, and the ML service still cannot flip it.

Requires a running database with the init scripts applied (the CI workflow stands one up via docker
compose). When no database is reachable it skips.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

import service_sql  # noqa: E402

# Watched by db-tenant-isolation.yml's path filter (tests/config_guards/test_ci_path_filters.py).
ROUTER = "services/api_gateway/routers/platform_switches.py"
KEY = "stateful_features_enabled"
ROLE = "api_gateway_user"


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


@pytest.fixture
def actor():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'set_platform_switch'")
    if cur.fetchone() is None:
        pytest.skip("set_platform_switch() not present — init scripts not applied")
    cur.execute("SELECT value FROM public.platform_config WHERE key = %s", (KEY,))
    before = cur.fetchone()[0]
    who = uuid.uuid4()
    try:
        yield cur, who
    finally:
        cur.execute("RESET ROLE")
        cur.execute("UPDATE public.platform_config SET value = %s::jsonb WHERE key = %s",
                    ("true" if before else "false", KEY))
        cur.execute("DELETE FROM public.platform_config_changes WHERE changed_by = %s", (str(who),))
        conn.close()


def _flip(value, who, key=KEY):
    # The route's statement and binding: the value as JSON text, the actor as a UUID.
    [row] = service_sql.run(service_sql.statement(ROUTER, "SWITCH_FLIP"),
                            {"key": key, "value": value, "actor_id": who}, role=ROLE)
    return row


def _changes(who):
    rows = service_sql.run(service_sql.statement(ROUTER, "CHANGES_SELECT"),
                           {"key": KEY, "before_id": None, "limit": 50}, role=ROLE)
    return [(r["old_value"], r["new_value"]) for r in rows if r["changed_by"] == who]


def test_a_flip_changes_the_switch_and_records_it_once(actor):
    cur, who = actor
    _flip("true", who)  # whatever it was, from a known state
    before = len(_changes(who))
    row = _flip("false", who)
    assert (row["changed"], row["value"]) == (True, False)
    assert _flip("false", who)["changed"] is False  # no action, no record
    assert _changes(who)[: len(_changes(who)) - before] == [(True, False)]
    switches = {r["key"]: r for r in service_sql.run(service_sql.statement(ROUTER, "SWITCHES_SELECT"), {}, role=ROLE)}
    assert switches[KEY]["value"] is False and switches[KEY]["updated_by"] == str(who)
    assert _flip("true", who)["changed"] is True
    assert _changes(who)[0] == (False, True)


@pytest.mark.parametrize("key, value", [("no_such_switch", "true"), (KEY, '"off"'), (KEY, "0")])
def test_an_unknown_switch_or_a_value_of_another_type_is_refused(actor, key, value):
    _cur, who = actor
    with pytest.raises(Exception) as refused:
        _flip(value, who, key=key)
    assert service_sql.sqlstate(refused.value) == "22023"


@pytest.mark.parametrize("role, sql", [
    (ROLE, "UPDATE public.platform_config SET value = 'false'::jsonb"),
    (ROLE, "INSERT INTO public.platform_config_changes (key, new_value, changed_by) "
           "VALUES ('x', 'true', gen_random_uuid())"),
    (ROLE, "DELETE FROM public.platform_config_changes"),
    ("ml_service_user", "UPDATE public.platform_config SET value = 'false'::jsonb"),
    ("ml_service_user", "SELECT * FROM public.set_platform_switch('stateful_features_enabled', 'false', gen_random_uuid())"),
])
def test_no_runtime_role_writes_the_switch_or_its_record_directly(actor, role, sql):
    cur, _who = actor
    cur.execute(f'SET ROLE "{role}"')
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            cur.execute(sql)
    finally:
        cur.execute("RESET ROLE")
