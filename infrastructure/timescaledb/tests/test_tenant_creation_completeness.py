# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every per-tenant shape migration reaches every tenant: those that exist and those created later.

A migration that changes the tenant schema does two things (ADR-0034 dec 3): it backfills the
schemas that exist when it runs, and it must be called from create_tenant_schema() so a tenant
created later gets the same shape. The second half is a list in 04-complete-tenant-creation.sql,
and forgetting to extend it fails silently: init and every existing tenant look right, and only a
tenant provisioned later lacks the columns — its first model registration fails. Migrations 18 and
19 shipped exactly so. Forgetting the first half is as silent the other way round: fresh installs
are right, and every live database lacks the shape until someone notices.

Static: reads the init scripts, needs no database.
"""
import glob
import os
import re

_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "init-scripts")
_CREATION = os.path.join(_SCRIPTS, "04-complete-tenant-creation.sql")


def _shape_functions():
    """add_* functions that take a tenant schema name: the per-tenant shape migrations."""
    found = {}
    for path in glob.glob(os.path.join(_SCRIPTS, "*.sql")):
        for name in re.findall(r"CREATE OR REPLACE FUNCTION (add_\w+)\(p_schema_name TEXT\)", open(path).read()):
            found[name] = os.path.basename(path)
    return found


def _script_number(script):
    return int(script.split("-", 1)[0])


def _create_tenant_schema_body():
    sql = open(_CREATION).read()
    start = sql.index("CREATE OR REPLACE FUNCTION create_tenant_schema(")
    return sql[start:sql.index("$$;", start)]


def test_the_shape_functions_are_found():
    assert {"add_alert_tables_to_schema", "add_alert_labels_to_schema",
            "add_display_name_column_to_sensors"} <= set(_shape_functions())


def test_create_tenant_schema_applies_every_shape_migration():
    body = _create_tenant_schema_body()
    missing = {f: src for f, src in _shape_functions().items() if f"PERFORM {f}(v_schema_name)" not in body}
    assert not missing, f"create_tenant_schema() never calls {missing}; new tenants lack that shape"


def test_every_later_shape_migration_backfills_existing_tenants():
    # Scripts before 04 run before create_tenant_schema() exists, so no tenant can predate them;
    # every later one must apply its own function to the tenant schemas already there.
    creation = _script_number(os.path.basename(_CREATION))
    missing = {}
    for f, src in _shape_functions().items():
        if _script_number(src) <= creation:
            continue
        sql = open(os.path.join(_SCRIPTS, src)).read()
        backfill = sql[sql.rfind("DO $$"):] if "DO $$" in sql else ""
        if f"PERFORM {f}(schema_name)" not in backfill:
            missing[f] = src
    assert not missing, f"never applied to existing tenant schemas: {missing}"


def test_create_tenant_schema_creates_the_sql_proxy_login_on_every_path():
    # ADR-0033 dec 1: provisioning creates the tenant's SQL proxy login, in its own transaction and
    # whether or not the notebooks profile is on. Gating it on the proxy principal existing, or
    # skipping it when the schema already exists, leaves it to the proxy's first use.
    body = _create_tenant_schema_body()
    assert "notebook_sql_proxy'" not in body, "the login must not be gated on the proxy principal"
    early_return = body[:body.index("RETURN v_schema_name;")]
    assert "PERFORM ensure_tenant_sql_login(p_company_id)" in early_return, (
        "re-provisioning an existing tenant does not converge its SQL login")
    assert body.count("PERFORM ensure_tenant_sql_login(p_company_id)") == 2, (
        "a new tenant's schema is built without its SQL login")
