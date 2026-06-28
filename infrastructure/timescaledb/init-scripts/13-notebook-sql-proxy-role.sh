#!/bin/bash

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Notebook SQL access proxy privileged role (ADR-0015 dec 5).
#
# The SQL proxy connects to TimescaleDB as ONE login role, then `SET ROLE`s into the requesting
# tenant's `tenant_reader_<uuid>` for the life of the kernel's session. That login role:
#   * is LOGIN (the proxy authenticates with it) but NOINHERIT, so it holds *no* tenant privilege
#     of its own — it has only what it explicitly SET ROLEs into. A bug that forgets to SET ROLE
#     therefore reads nothing, rather than every tenant (fail-closed).
#   * is a member of every per-tenant reader role, which is what makes `SET ROLE` succeed.
#
# Created only when NOTEBOOK_SQL_PROXY_DB_PASSWORD is set (the `notebooks` profile). A base
# deployment without notebooks never creates the role. Runs after the tenant-reader migration
# (09) so the existing readers are present to grant; new tenants are granted at creation time by
# 04-complete-tenant-creation.sql.

set -e

if [ -z "${NOTEBOOK_SQL_PROXY_DB_PASSWORD:-}" ]; then
  echo "NOTEBOOK_SQL_PROXY_DB_PASSWORD unset — skipping notebook_sql_proxy role (notebooks profile off)."
  exit 0
fi

echo "=========================================="
echo "Creating notebook_sql_proxy role (ADR-0015)"
echo "=========================================="

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    DECLARE
        r RECORD;
    BEGIN
        -- LOGIN + NOINHERIT: authenticates, but carries no tenant privilege until SET ROLE.
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'notebook_sql_proxy') THEN
            EXECUTE format(
                'CREATE ROLE notebook_sql_proxy WITH LOGIN NOINHERIT PASSWORD %L',
                '${NOTEBOOK_SQL_PROXY_DB_PASSWORD}'
            );
        ELSE
            EXECUTE format(
                'ALTER ROLE notebook_sql_proxy WITH LOGIN NOINHERIT PASSWORD %L',
                '${NOTEBOOK_SQL_PROXY_DB_PASSWORD}'
            );
        END IF;

        -- Bounded connections (one backend per active authoring kernel); in line with the
        -- per-service limits in 00-create-roles.sh / migration 11.
        EXECUTE 'ALTER ROLE notebook_sql_proxy CONNECTION LIMIT 50';

        -- Grant membership in every existing per-tenant reader so the proxy can SET ROLE into it.
        FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'tenant_reader_%' LOOP
            EXECUTE format('GRANT %I TO notebook_sql_proxy', r.rolname);
        END LOOP;
    END
    \$\$;
EOSQL

echo "  ✓ notebook_sql_proxy ready (LOGIN NOINHERIT, member of all tenant_reader_* roles)"
