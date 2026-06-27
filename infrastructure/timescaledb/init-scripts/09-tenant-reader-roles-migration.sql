-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Per-Tenant Read-Only Role Migration
-- Version: 1.0
-- Date: 2026-06-27
-- Purpose: Backfill the per-tenant read-only role (tenant_reader_<uuid>) for every tenant
--          schema provisioned before create_tenant_schema() began creating it (04-...).
--          Idempotent: safe to run repeatedly and on a fresh database (where it is a no-op
--          until tenants exist). See ADR-0011 dec 3 and ADR-0012 dec 4.
--
-- The reader role is NOLOGIN, read-only, and scoped to a single tenant schema: a cross-tenant
-- query made as this role fails at the GRANT level rather than relying on search_path
-- discipline. The notebook SQL proxy (ADR-0012) assumes it via SET ROLE, so no password
-- exists to place in an untrusted kernel.

-- =============================================================================
-- ENSURE THE READER ROLE EXISTS AND IS SCOPED TO ONE SCHEMA
-- =============================================================================

CREATE OR REPLACE FUNCTION ensure_tenant_reader_role(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    -- 'tenant_' is exactly 7 characters; the remainder is the underscored company UUID.
    v_reader_role TEXT := 'tenant_reader_' || substr(p_schema_name, 8);
BEGIN
    -- No CREATE ROLE IF NOT EXISTS in PostgreSQL; guard on pg_roles.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_reader_role) THEN
        EXECUTE format('CREATE ROLE %I NOLOGIN', v_reader_role);
    END IF;

    -- Read-only, single-schema. No INSERT/UPDATE/DELETE, no sequence access. TimescaleDB
    -- propagates the table SELECT to the hypertables' chunks automatically.
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', p_schema_name, v_reader_role);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', p_schema_name, v_reader_role);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO %I', p_schema_name, v_reader_role);

    RAISE NOTICE '  ✓ Reader role % ensured for schema %', v_reader_role, p_schema_name;
END;
$$;

COMMENT ON FUNCTION ensure_tenant_reader_role IS
    'Idempotently creates and grants the NOLOGIN, read-only, single-schema tenant_reader_<uuid> role (ADR-0011/0012)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    ensured_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Per-Tenant Read-Only Role Migration';
    RAISE NOTICE '========================================';

    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
        ORDER BY nspname
    LOOP
        PERFORM ensure_tenant_reader_role(schema_name);
        ensured_count := ensured_count + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Tenant schemas processed: %', ensured_count;
    RAISE NOTICE '  - Role: tenant_reader_<uuid> (NOLOGIN, SELECT only, single schema)';
    RAISE NOTICE '========================================';
END $$;

-- =============================================================================
-- VERIFICATION QUERY
-- =============================================================================

-- Run this to confirm each tenant schema has a read-only reader role and that the role
-- holds no write privilege anywhere:
/*
DO $$
DECLARE
    schema_name TEXT;
    v_reader_role TEXT;
    has_select BOOLEAN;
    has_write BOOLEAN;
BEGIN
    FOR schema_name IN
        SELECT nspname FROM pg_namespace WHERE nspname LIKE 'tenant_%' ORDER BY nspname LIMIT 5
    LOOP
        v_reader_role := 'tenant_reader_' || substr(schema_name, 8);

        SELECT EXISTS (
            SELECT 1 FROM information_schema.table_privileges
            WHERE table_schema = schema_name AND grantee = v_reader_role AND privilege_type = 'SELECT'
        ) INTO has_select;

        SELECT EXISTS (
            SELECT 1 FROM information_schema.table_privileges
            WHERE grantee = v_reader_role AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
        ) INTO has_write;

        RAISE NOTICE 'Schema: % | role: % | SELECT: % | any write: %',
            schema_name, v_reader_role, has_select, has_write;
    END LOOP;
END $$;
*/
