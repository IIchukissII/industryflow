# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quota overrides (init script 38, ADR-0049), against the real database, as the API gateway's
and the alert worker's own roles with their own statements on their drivers (service_sql.py):

  * an override set changes the tenant's cap and appends one record, with its actor, before and after,
  * a value equal to the current one changes and records nothing; NULL returns the tenant to the default,
  * an unknown cap, a value out of range, or a tenant that does not exist is refused, and every cap of
    the contract is one the database takes,
  * no runtime role writes the overrides or their record directly, and the alert worker reads them,
  * the deletion job removes a tenant's overrides with its database objects, and keeps their record (dec 7),
  * the measurement job measures a tenant's database bytes, its hypertables' chunks included, and records
    each store's measurement only through its two functions; the alert worker reads them; no other role
    records one, and a deleted tenant's measurements go with it (dec 11, 7).

Requires a running database with the init scripts applied (the CI workflow stands one up via docker
compose). When no database is reachable it skips.
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

import service_sql  # noqa: E402
from industryflow_contracts import testing  # noqa: E402

# Watched by db-tenant-isolation.yml's path filter (tests/config_guards/test_ci_path_filters.py).
ROUTER = "services/api_gateway/routers/tenant_quotas.py"
PUBLISHER = "services/alert_service/worker/tenant_status_publisher.py"
GATEWAY = "api_gateway_user"
WORKER = "alert_service_user"
JOB = "tenant_delete_user"
USAGE_JOB = "services/tenant_usage/source.py"
USAGE = "tenant_usage_user"


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


@pytest.fixture
def tenant():
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no DB here"
        pytest.skip(f"no database reachable: {e}")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'set_tenant_quota'")
    if cur.fetchone() is None:
        pytest.skip("set_tenant_quota() not present — init scripts not applied")
    [(company_id, *_)] = _as(cur, GATEWAY, "SELECT * FROM public.provision_company(%s)",
                             (f"quota-{uuid.uuid4().hex[:8]}",))
    company_id = uuid.UUID(str(company_id))
    try:
        yield cur, company_id, uuid.uuid4()
    finally:
        suffix = str(company_id).replace("-", "_")
        cur.execute("RESET ROLE")
        cur.execute("DELETE FROM public.tenant_quota_overrides WHERE company_id = %s", (str(company_id),))
        cur.execute("DELETE FROM public.tenant_quota_changes WHERE company_id = %s", (str(company_id),))
        cur.execute("DELETE FROM public.tenant_storage_usage WHERE company_id = %s", (str(company_id),))
        for table in ("company_deletion_steps", "company_deletions", "company_lifecycle_events"):
            cur.execute(f"DELETE FROM public.{table} WHERE company_id = %s", (str(company_id),))
        cur.execute(testing.drop_tenant_schema_sql(f'tenant_{suffix}'))
        cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
        cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
        cur.execute("DELETE FROM public.companies WHERE company_id = %s", (str(company_id),))
        conn.close()


def _set(company_id, cap, value, who, capacity=None):
    # The route's statement and binding: the tenant and actor as UUIDs, the value an int or None, the
    # capacity an int or None (none derived).
    [row] = service_sql.run(service_sql.statement(ROUTER, "QUOTA_SET"),
                            {"company_id": company_id, "cap": cap, "value": value, "actor_id": who,
                             "capacity": capacity}, role=GATEWAY)
    return row


def _others(company_id, cap):
    [row] = service_sql.run(service_sql.statement(ROUTER, "OTHERS_SELECT"),
                            {"company_id": company_id, "cap": cap}, role=GATEWAY)
    return int(row["others"])


def test_the_overrides_of_a_derived_capacity_never_exceed_it(tenant):
    """ADR-0049 dec 14: an override that would take the overrides of every tenant past the capacity is
    refused and sets nothing; one that is lowered is taken even past it, so a shrunk capacity can be met."""
    _cur, company_id, who = tenant
    cap = "readings_per_second"
    others = _others(company_id, cap)
    capacity = others + 100
    assert _set(company_id, cap, 100, who, capacity)["changed"] is True
    with pytest.raises(Exception) as refused:
        _set(company_id, cap, 101, who, capacity)
    assert service_sql.sqlstate(refused.value) == "53400"
    assert _overrides(company_id) == {cap: 100}
    # The resources shrank: lowering is taken although the overrides still exceed the capacity, raising is not.
    assert _set(company_id, cap, 60, who, others + 10)["value"] == 60
    with pytest.raises(Exception) as refused:
        _set(company_id, cap, 61, who, others + 10)
    assert service_sql.sqlstate(refused.value) == "53400"
    # Where no capacity is derived, an amount holds as before.
    assert _set(company_id, cap, 1000, who)["value"] == 1000
    # The gateway's platform metric reads every tenant's overrides of the cap together, as its role.
    reserved = service_sql.statement("services/api_gateway/messaging/quota_metrics.py", "RESERVED_SELECT")
    rows = {r["cap"]: int(r["reserved"]) for r in service_sql.run(reserved, {}, role=GATEWAY)}
    assert rows[cap] == others + 1000


def _overrides(company_id):
    rows = service_sql.run(service_sql.statement(ROUTER, "OVERRIDES_SELECT"),
                           {"company_id": str(company_id)}, role=GATEWAY)
    return {r["cap"]: r["value"] for r in rows}


def _changes(company_id):
    rows = service_sql.run(service_sql.statement(ROUTER, "CHANGES_SELECT"),
                           {"company_id": company_id, "before_id": None, "limit": 50}, role=GATEWAY)
    return [(r["cap"], r["old_value"], r["new_value"], r["changed_by"]) for r in rows]


def test_an_override_is_set_changed_and_cleared_and_each_change_recorded_once(tenant):
    _cur, company_id, who = tenant
    assert _set(company_id, "readings_per_second", 40, who)["changed"] is True
    assert _set(company_id, "readings_per_second", 40, who)["changed"] is False  # no change, no record
    assert _set(company_id, "readings_per_second", 90, who)["value"] == 90
    assert _overrides(company_id) == {"readings_per_second": 90}
    row = _set(company_id, "readings_per_second", None, who)
    assert (row["changed"], row["value"]) == (True, None)
    assert _set(company_id, "readings_per_second", None, who)["changed"] is False
    assert _overrides(company_id) == {}
    assert _changes(company_id) == [("readings_per_second", 90, None, who), ("readings_per_second", 40, 90, who),
                                    ("readings_per_second", None, 40, who)]


@pytest.mark.parametrize("cap, value, state", [
    ("disk", 5, "22023"),
    ("readings_per_second", 0, "22023"),
    ("notebook_environments", 1_000_001, "22023"),
])
def test_an_unknown_cap_or_a_value_out_of_range_is_refused(tenant, cap, value, state):
    _cur, company_id, who = tenant
    with pytest.raises(Exception) as refused:
        _set(company_id, cap, value, who)
    assert service_sql.sqlstate(refused.value) == state


def test_every_cap_of_the_contract_is_one_the_database_takes(tenant):
    """The database's list of caps is the contract's: an override of each is set, and its record kept."""
    from industryflow_contracts import quotas

    cur, company_id, who = tenant
    for cap in quotas.CAPS:
        assert _set(company_id, cap, 7, who)["changed"] is True, cap
    assert _overrides(company_id) == {cap: 7 for cap in quotas.CAPS}
    cur.execute("SELECT cap FROM public.tenant_quota_caps")
    assert {r[0] for r in cur.fetchall()} == set(quotas.CAPS)


def test_a_tenant_that_does_not_exist_is_refused(tenant):
    _cur, _company_id, who = tenant
    with pytest.raises(Exception) as refused:
        _set(uuid.uuid4(), "readings_per_second", 5, who)
    assert service_sql.sqlstate(refused.value) == "P0002"


def test_the_alert_worker_reads_the_overrides_with_its_own_statement(tenant):
    _cur, company_id, who = tenant
    _set(company_id, "notebook_environments", 2, who)
    rows = service_sql.run(service_sql.statement(PUBLISHER, "OVERRIDES_SELECT"), (), role=WORKER)
    assert {"company_id": company_id, "cap": "notebook_environments", "value": 2} in [dict(r) for r in rows]


@pytest.mark.parametrize("role, sql", [
    (GATEWAY, "INSERT INTO public.tenant_quota_overrides (company_id, cap, value, updated_by) "
              "SELECT company_id, 'readings_per_second', 5, gen_random_uuid() FROM public.companies LIMIT 1"),
    (GATEWAY, "UPDATE public.tenant_quota_overrides SET value = 5"),
    (GATEWAY, "DELETE FROM public.tenant_quota_changes"),
    (WORKER, "UPDATE public.tenant_quota_overrides SET value = 5"),
    (WORKER, "SELECT updated_by FROM public.tenant_quota_overrides"),
    (WORKER, "SELECT * FROM public.set_tenant_quota(gen_random_uuid(), 'readings_per_second', 5, gen_random_uuid(), NULL)"),
])
def test_no_runtime_role_writes_the_overrides_or_their_record_directly(tenant, role, sql):
    cur, _company_id, _who = tenant
    with pytest.raises(errors.InsufficientPrivilege):
        _as(cur, role, sql)


def test_deleting_the_tenant_deletes_its_overrides_and_keeps_their_record(tenant):
    cur, company_id, who = tenant
    _set(company_id, "readings_per_second", 40, who)
    _set(company_id, "notebook_environments", 2, who)
    cid = str(company_id)
    _as(cur, GATEWAY, "SELECT * FROM public.update_company(%s, NULL, false)", (cid,))
    cur.execute("UPDATE public.companies SET suspended_at = now() - interval '31 days' WHERE company_id = %s", (cid,))
    _as(cur, GATEWAY, "SELECT * FROM public.request_company_deletion(%s, %s)", (cid, str(who)))
    [(stores,)] = _as(cur, JOB, "SELECT public.company_deletion_required_stores()")
    for store in stores:
        _as(cur, JOB, "SELECT public.record_company_deletion_step(%s, %s, NULL, '{}'::jsonb)", (cid, store))
    [(detail,)] = _as(cur, JOB, "SELECT public.complete_company_deletion(%s, interval '14 days')", (cid,))
    assert detail["quota_overrides"] == 2
    cur.execute("SELECT (SELECT count(*) FROM public.tenant_quota_overrides WHERE company_id = %s), "
                "(SELECT count(*) FROM public.tenant_quota_changes WHERE company_id = %s)", (cid, cid))
    assert cur.fetchone() == (0, 2)
    # A deleted tenant takes no new override.
    with pytest.raises(Exception) as refused:
        _set(company_id, "readings_per_second", 5, who)
    assert service_sql.sqlstate(refused.value) == "P0002"



def test_the_job_measures_a_tenants_chunks_and_records_through_its_functions(tenant):
    cur, company_id, _who = tenant
    cid = str(company_id)
    cur.execute("SELECT schema_name FROM public.companies WHERE company_id = %s", (cid,))
    [(schema,)] = cur.fetchall()
    measure = service_sql.statement(USAGE_JOB, "MEASURE")

    def measured():
        return next(r["bytes"] for r in service_sql.run(measure, (), role=USAGE) if str(r["company_id"]) == cid)

    empty = measured()
    # Readings land in chunks outside the schema; the measurement grows with them.
    cur.execute(f'''INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count)
                    VALUES ('pump', 'p1', 1) RETURNING equipment_id''')
    [(equipment,)] = cur.fetchall()
    cur.execute(f'''INSERT INTO "{schema}".sensors (equipment_id, sensor_name, sensor_type, position)
                    VALUES (%s, 's1', 'temperature', 1) RETURNING sensor_id''', (equipment,))
    [(sensor,)] = cur.fetchall()
    cur.execute(f'''INSERT INTO "{schema}".sensor_measurements (time, sensor_id, equipment_id, site_id, value)
                    SELECT now() - make_interval(secs => g), %s, %s, 'site', g
                    FROM generate_series(1, 20000) g''', (sensor, equipment))
    grown = measured()
    assert grown > empty + 500_000, (empty, grown)

    record = service_sql.statement(USAGE_JOB, "RECORD")
    service_sql.run(record, (cid, "database", grown), role=USAGE)
    service_sql.run(record, (cid, "object", 42), role=USAGE)
    rows = service_sql.run(service_sql.statement(PUBLISHER, "USAGE_SELECT"), (), role=WORKER)
    mine = {r["store"]: r for r in rows if str(r["company_id"]) == cid}
    assert mine["object"]["bytes"] == 42 and mine["database"]["bytes"] == grown
    assert 0 <= mine["object"]["age"] < 60


@pytest.mark.parametrize("role, sql", [
    (GATEWAY, "SELECT public.record_tenant_storage_usage(gen_random_uuid(), 'object', 1)"),
    (WORKER, "SELECT public.record_tenant_storage_usage(gen_random_uuid(), 'object', 1)"),
    (WORKER, "UPDATE public.tenant_storage_usage SET bytes = 0"),
    (USAGE, "SELECT * FROM public.tenant_storage_usage"),
    (USAGE, "DELETE FROM public.tenant_storage_usage"),
    (USAGE, "SELECT * FROM public.companies"),
])
def test_only_the_job_records_a_measurement_and_only_through_its_function(tenant, role, sql):
    cur, _company_id, _who = tenant
    with pytest.raises(errors.InsufficientPrivilege):
        _as(cur, role, sql)


def test_an_unknown_store_or_a_negative_size_is_refused(tenant):
    cur, company_id, _who = tenant
    for store, size in (("disk", 1), ("object", -1)):
        with pytest.raises(Exception) as refused:
            _as(cur, USAGE, "SELECT public.record_tenant_storage_usage(%s, %s, %s)", (str(company_id), store, size))
        assert service_sql.sqlstate(refused.value) == "22023"


def test_deleting_the_tenant_deletes_its_measurements(tenant):
    cur, company_id, who = tenant
    cid = str(company_id)
    _as(cur, USAGE, "SELECT public.record_tenant_storage_usage(%s, 'object', 7)", (cid,))
    _as(cur, GATEWAY, "SELECT * FROM public.update_company(%s, NULL, false)", (cid,))
    cur.execute("UPDATE public.companies SET suspended_at = now() - interval '31 days' WHERE company_id = %s", (cid,))
    _as(cur, GATEWAY, "SELECT * FROM public.request_company_deletion(%s, %s)", (cid, str(who)))
    [(stores,)] = _as(cur, JOB, "SELECT public.company_deletion_required_stores()")
    for store in stores:
        _as(cur, JOB, "SELECT public.record_company_deletion_step(%s, %s, NULL, '{}'::jsonb)", (cid, store))
    _as(cur, JOB, "SELECT public.complete_company_deletion(%s, interval '14 days')", (cid,))
    cur.execute("SELECT count(*) FROM public.tenant_storage_usage WHERE company_id = %s", (cid,))
    assert cur.fetchone() == (0,)
    # A run that overlaps the deletion records nothing for it.
    _as(cur, USAGE, "SELECT public.record_tenant_storage_usage(%s, 'object', 7)", (cid,))
    cur.execute("SELECT count(*) FROM public.tenant_storage_usage WHERE company_id = %s", (cid,))
    assert cur.fetchone() == (0,)


def test_the_gateway_reads_a_tenants_measurements_and_counts_the_unmeasured(tenant):
    cur, company_id, _who = tenant
    cid = str(company_id)
    counter = service_sql.statement("services/api_gateway/messaging/quota_metrics.py", "UNMEASURED_SELECT")
    # Provisioned long ago and never measured: counted; once both stores are measured: not.
    cur.execute("UPDATE public.companies SET created_at = now() - interval '1 day' WHERE company_id = %s", (cid,))
    [before] = service_sql.run(counter, {"stale": 3600}, role=GATEWAY)
    for store in ("database", "object"):
        _as(cur, USAGE, "SELECT public.record_tenant_storage_usage(%s, %s, 1)", (cid, store))
    [after] = service_sql.run(counter, {"stale": 3600}, role=GATEWAY)
    assert before["unmeasured"] - after["unmeasured"] == 1
    rows = service_sql.run(service_sql.statement(ROUTER, "USAGE_SELECT"), {"company_id": company_id}, role=GATEWAY)
    assert {r["store"] for r in rows} == {"database", "object"}
