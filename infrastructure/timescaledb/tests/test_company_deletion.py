# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The deletion request and the lifecycle record (25-company-deletion.sql, ADR-0033 dec 4, 6, 7).

Delete is irreversible, so the database itself holds every rule the request must meet: only a
suspended tenant, only after the wait, only once, never undone by a later reactivation. The
lifecycle record outlives the tenant and is append-only. Each call here runs as api_gateway_user
(SET ROLE), exactly as the gateway does.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one
up); skips when none is reachable.
"""
import json
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

GATEWAY = "api_gateway_user"


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", os.getenv("DB_NAME", "industryflow")),
        user=os.getenv("PGUSER", os.getenv("DB_USER", "postgres")),
        password=os.getenv("PGPASSWORD", os.getenv("DB_PASSWORD", "postgres")),
        connect_timeout=int(os.getenv("PG_CONNECT_TIMEOUT", "5")),
    )


def _as(cur, role, sql, args=()):
    cur.execute(f'SET ROLE "{role}"')
    try:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else None
    finally:
        cur.execute("RESET ROLE")


@pytest.fixture(scope="module")
def db():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'request_company_deletion'")
    if cur.fetchone() is None:
        pytest.skip("request_company_deletion() not present — init scripts not applied")
    created = []
    yield cur, created
    for company_id in created:
        suffix = str(company_id).replace("-", "_")
        cur.execute("DELETE FROM public.company_deletion_steps WHERE company_id = %s", (str(company_id),))
        cur.execute("DELETE FROM public.company_deletions WHERE company_id = %s", (str(company_id),))
        cur.execute("DELETE FROM public.company_lifecycle_events WHERE company_id = %s", (str(company_id),))
        cur.execute(f'DROP SCHEMA IF EXISTS "tenant_{suffix}" CASCADE')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        cur.execute("DELETE FROM public.companies WHERE company_id = %s", (str(company_id),))
    conn.close()


@pytest.fixture
def company(db):
    cur, created = db
    [(company_id, *_)] = _as(cur, GATEWAY, "SELECT * FROM public.provision_company(%s)",
                             (f"deletion-{uuid.uuid4().hex[:8]}",))
    created.append(company_id)
    return cur, str(company_id)


def _set_active(cur, company_id, active):
    _as(cur, GATEWAY, "SELECT * FROM public.update_company(%s, NULL, %s)", (company_id, active))


def _suspended_at(cur, company_id):
    cur.execute("SELECT suspended_at FROM public.companies WHERE company_id = %s", (company_id,))
    return cur.fetchone()[0]


def _backdate_suspension(cur, company_id, days):
    # As the superuser: only the clock is moved, the way 30 days of waiting would.
    cur.execute("UPDATE public.companies SET suspended_at = now() - make_interval(days => %s) "
                "WHERE company_id = %s", (days, company_id))


def _request(cur, company_id, actor):
    return _as(cur, GATEWAY, "SELECT * FROM public.request_company_deletion(%s, %s)", (company_id, actor))


def test_suspension_starts_the_clock_and_reactivation_stops_it(company):
    cur, company_id = company
    assert _suspended_at(cur, company_id) is None
    _set_active(cur, company_id, False)
    started = _suspended_at(cur, company_id)
    assert started is not None
    _set_active(cur, company_id, False)  # suspending again does not restart the wait
    assert _suspended_at(cur, company_id) == started
    _set_active(cur, company_id, True)
    assert _suspended_at(cur, company_id) is None


def test_an_active_tenant_cannot_be_deleted(company):
    cur, company_id = company
    with pytest.raises(errors.ObjectNotInPrerequisiteState) as e:
        _request(cur, company_id, str(uuid.uuid4()))
    assert e.value.diag.message_hint == "not_suspended"


def test_a_tenant_suspended_for_less_than_the_wait_cannot_be_deleted(company):
    cur, company_id = company
    _set_active(cur, company_id, False)
    _backdate_suspension(cur, company_id, 29)
    with pytest.raises(errors.ObjectNotInPrerequisiteState) as e:
        _request(cur, company_id, str(uuid.uuid4()))
    assert e.value.diag.message_hint == "suspended_too_recently"


def test_an_unknown_company_cannot_be_deleted(db):
    cur, _ = db
    with pytest.raises(errors.NoDataFound):
        _request(cur, str(uuid.uuid4()), str(uuid.uuid4()))


def test_a_request_needs_its_actor(company):
    cur, company_id = company
    _set_active(cur, company_id, False)
    _backdate_suspension(cur, company_id, 31)
    with pytest.raises(errors.NullValueNotAllowed):
        _request(cur, company_id, None)


def test_a_request_after_the_wait_is_recorded_once_and_is_irreversible(company):
    cur, company_id = company
    actor = str(uuid.uuid4())
    _set_active(cur, company_id, False)
    _backdate_suspension(cur, company_id, 31)

    [(got, requested_at, requested_by)] = _request(cur, company_id, actor)
    assert (str(got), str(requested_by)) == (company_id, actor) and requested_at is not None
    events = _as(cur, GATEWAY, "SELECT action, actor_id FROM public.company_lifecycle_events "
                               "WHERE company_id = %s ORDER BY event_id", (company_id,))
    assert (("delete_requested", uuid.UUID(actor)) in [(a, uuid.UUID(str(b))) for a, b in events])

    with pytest.raises(errors.UniqueViolation):
        _request(cur, company_id, actor)
    for active in (True, False):
        with pytest.raises(errors.ObjectNotInPrerequisiteState) as e:
            _set_active(cur, company_id, active)
        assert e.value.diag.message_hint == "deletion_requested"
    with pytest.raises(errors.ObjectNotInPrerequisiteState):
        _as(cur, GATEWAY, "SELECT * FROM public.update_company(%s, %s, NULL)", (company_id, "renamed"))


def test_the_gateway_records_actions_only_through_the_function(company):
    cur, company_id = company
    actor = str(uuid.uuid4())
    [(event_id,)] = _as(cur, GATEWAY, "SELECT public.record_company_lifecycle(%s, %s, %s, %s::jsonb)",
                        (company_id, "rename", actor, json.dumps({"via": "test"})))
    assert event_id
    for statement, args in (
        ("INSERT INTO public.company_lifecycle_events (company_id, action) VALUES (%s, 'create')", (company_id,)),
        ("UPDATE public.company_lifecycle_events SET action = 'create' WHERE event_id = %s", (event_id,)),
        ("DELETE FROM public.company_lifecycle_events WHERE event_id = %s", (event_id,)),
        ("INSERT INTO public.company_deletions (company_id, requested_by) VALUES (%s, %s)", (company_id, actor)),
        ("INSERT INTO public.company_deletion_steps (company_id, store) VALUES (%s, 'x')", (company_id,)),
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            _as(cur, GATEWAY, statement, args)


def test_the_record_accepts_only_lifecycle_actions(company):
    cur, company_id = company
    with pytest.raises(errors.CheckViolation):
        _as(cur, GATEWAY, "SELECT public.record_company_lifecycle(%s, %s, %s)",
            (company_id, "drop_everything", str(uuid.uuid4())))
