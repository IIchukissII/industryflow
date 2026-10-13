#!/bin/bash

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Notebook SQL access proxy principals (ADR-0015 dec 5).
#
# The session a kernel's bytes reach is confined to ONE tenant by Postgres itself, not by what the
# proxy lets through. The proxy relays the kernel's wire protocol verbatim, so whatever the upstream
# session user may do, the kernel may do: `SET ROLE`, `RESET ROLE`, `set_config('role', …)`,
# `SET SESSION AUTHORIZATION`. The upstream login must therefore hold nothing beyond its tenant.
#
#   * tenant_sqlproxy_<uuid> — one LOGIN role per tenant, the only principal a kernel session is
#     ever opened as. NOINHERIT, member of exactly its own tenant's tenant_reader_<uuid> (which it
#     SET ROLEs into) and of the privilege-free `notebook_sql_tenants` group (which exists only so
#     pg_hba can name every tenant login in one rule). RESET ROLE lands back on a principal that
#     reads nothing; SET ROLE into any other tenant's reader is refused, because it is not a member.
#     Its password is derived by the proxy from a key the database never sees; the database holds
#     only the SCRAM verifier.
#   * notebook_sql_proxy — the proxy's enrolment principal. LOGIN, a member of NOTHING, and allowed
#     exactly one act: EXECUTE on notebook_sql_proxy_enrol(), which installs a tenant login's SCRAM
#     verifier. It is cross-tenant authority, so no kernel byte ever reaches a session opened as it:
#     the proxy opens one only to enrol, runs the one call, and closes it.
#
# The functions are defined unconditionally (create_tenant_schema() resolves them at call time);
# the principals are created only when NOTEBOOK_SQL_PROXY_DB_PASSWORD is set (the `notebooks`
# profile). Idempotent and re-runnable: on an existing database, running this script again is the
# migration from the earlier design, where notebook_sql_proxy was a member of every tenant reader
# — it revokes every such membership and provisions a login role for every existing tenant.

set -e

echo "=========================================="
echo "Notebook SQL proxy principals (ADR-0015)"
echo "=========================================="

# Quoted heredoc: no shell expansion, so the dollar-quoting below reaches psql verbatim.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    -- One tenant's SQL login, created or re-asserted with exactly the memberships it may hold.
    CREATE OR REPLACE FUNCTION ensure_tenant_sql_login(p_company_id UUID)
    RETURNS TEXT
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_suffix TEXT := replace(p_company_id::TEXT, '-', '_');
        v_reader TEXT := 'tenant_reader_' || v_suffix;
        v_login  TEXT := 'tenant_sqlproxy_' || v_suffix;
        r RECORD;
    BEGIN
        -- A login exists only for a provisioned tenant: no reader role, no tenant.
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_reader) THEN
            RAISE EXCEPTION 'no provisioned tenant %', p_company_id USING ERRCODE = '42704';
        END IF;

        -- Holds no privilege and must never be granted one: every tenant login is a member.
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'notebook_sql_tenants') THEN
            CREATE ROLE notebook_sql_tenants NOLOGIN;
        END IF;

        -- No password here: the role cannot authenticate until the proxy enrols its verifier.
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_login) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
                'NOBYPASSRLS CONNECTION LIMIT 20', v_login);
        ELSE
            EXECUTE format(
                'ALTER ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
                'NOBYPASSRLS CONNECTION LIMIT 20', v_login);
        END IF;

        -- Exactly two memberships. Anything else this login can SET ROLE into is a way out of
        -- its tenant, so it is revoked rather than tolerated.
        FOR r IN
            SELECT g.rolname
            FROM pg_auth_members m
            JOIN pg_roles g ON g.oid = m.roleid
            JOIN pg_roles u ON u.oid = m.member
            WHERE u.rolname = v_login AND g.rolname NOT IN (v_reader, 'notebook_sql_tenants')
        LOOP
            EXECUTE format('REVOKE %I FROM %I', r.rolname, v_login);
        END LOOP;
        EXECUTE format('GRANT %I TO %I', v_reader, v_login);
        EXECUTE format('GRANT notebook_sql_tenants TO %I', v_login);

        RETURN v_login;
    END;
    $$;
    REVOKE ALL ON FUNCTION ensure_tenant_sql_login(UUID) FROM PUBLIC;
    COMMENT ON FUNCTION ensure_tenant_sql_login(UUID) IS
        'Creates or re-asserts tenant_sqlproxy_<uuid>: LOGIN NOINHERIT, member of only its own tenant reader + notebook_sql_tenants (ADR-0015 dec 5)';

    -- The proxy's one cross-tenant act: install a tenant login's SCRAM verifier. Takes a verifier,
    -- never a password, so no plaintext credential reaches the database or its logs.
    CREATE OR REPLACE FUNCTION notebook_sql_proxy_enrol(p_company_id UUID, p_verifier TEXT)
    RETURNS TEXT
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $$
    DECLARE
        v_login TEXT;
    BEGIN
        IF p_verifier IS NULL OR p_verifier !~
            '^SCRAM-SHA-256\$[0-9]{4,7}:[A-Za-z0-9+/]+=*\$[A-Za-z0-9+/]+=*:[A-Za-z0-9+/]+=*$' THEN
            RAISE EXCEPTION 'not a SCRAM-SHA-256 verifier' USING ERRCODE = '22023';
        END IF;
        v_login := public.ensure_tenant_sql_login(p_company_id);
        EXECUTE format('ALTER ROLE %I PASSWORD %L', v_login, p_verifier);
        RETURN v_login;
    END;
    $$;
    REVOKE ALL ON FUNCTION notebook_sql_proxy_enrol(UUID, TEXT) FROM PUBLIC;
    COMMENT ON FUNCTION notebook_sql_proxy_enrol(UUID, TEXT) IS
        'Installs the SCRAM verifier of one tenant SQL login; EXECUTE is granted to notebook_sql_proxy only (ADR-0015 dec 5)';
EOSQL

if [ -z "${NOTEBOOK_SQL_PROXY_DB_PASSWORD:-}" ]; then
  echo "NOTEBOOK_SQL_PROXY_DB_PASSWORD unset — skipping the proxy principals (notebooks profile off)."
  exit 0
fi

# psql reads the password from the environment (\getenv) and quotes it itself (:'pw'): it is never
# spliced into the SQL text by the shell, so no character in it can change the statement, and it is
# never on a command line.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    \getenv pw NOTEBOOK_SQL_PROXY_DB_PASSWORD
    SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'notebook_sql_proxy') AS create_proxy \gset
    \if :create_proxy
        CREATE ROLE notebook_sql_proxy;
    \endif
    ALTER ROLE notebook_sql_proxy WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOREPLICATION NOBYPASSRLS PASSWORD :'pw';
    -- Enrolment only: one short connection per tenant whose login is not yet enrolled.
    ALTER ROLE notebook_sql_proxy CONNECTION LIMIT 5;

    DO $$
    DECLARE
        r RECORD;
        n INTEGER := 0;
    BEGIN
        -- A member of nothing. The earlier design granted it every tenant reader; that is exactly
        -- the reach a relayed kernel inherited, so every membership goes.
        FOR r IN
            SELECT g.rolname
            FROM pg_auth_members m
            JOIN pg_roles g ON g.oid = m.roleid
            JOIN pg_roles u ON u.oid = m.member
            WHERE u.rolname = 'notebook_sql_proxy'
        LOOP
            EXECUTE format('REVOKE %I FROM notebook_sql_proxy', r.rolname);
        END LOOP;

        -- A login role for every tenant provisioned before this ran (new tenants get theirs from
        -- create_tenant_schema()).
        FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'tenant\_reader\_%' LOOP
            PERFORM ensure_tenant_sql_login(replace(substr(r.rolname, 15), '_', '-')::UUID);
            n := n + 1;
        END LOOP;
        RAISE NOTICE '  ✓ tenant SQL logins ensured: %', n;
    END
    $$;

    GRANT EXECUTE ON FUNCTION notebook_sql_proxy_enrol(UUID, TEXT) TO notebook_sql_proxy;
EOSQL

echo "  ✓ notebook_sql_proxy ready (LOGIN, member of nothing, EXECUTE on notebook_sql_proxy_enrol only)"
echo "  ✓ tenant_sqlproxy_<uuid> logins ready (one per tenant, confined to its own reader)"
