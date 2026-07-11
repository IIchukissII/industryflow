-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Cold-Layer Raw Retention Migration (ADR-0025)
-- Version: 1.0
-- Date: 2026-07-11
-- Purpose: Move raw-measurement retention from a blind TimescaleDB timer to the cold-export job's
--          export -> verify -> drop path (ADR-0025 dec 2/3), and give the exporter the least
--          privilege it needs. Idempotent; safe to run repeatedly and on a fresh database (where
--          it is a no-op until tenants exist). Mirrors 09-tenant-reader-roles-migration.sql.
--
-- After this migration NO timer drops raw chunks. The cold-export job (services/cold_export) is
-- the sole authority that drops a raw chunk, and only after a verified Parquet export. If that
-- job is not deployed, raw simply accumulates (recoverable) rather than being dropped un-archived
-- (not recoverable) — the deliberate trade of ADR-0025 dec 3.

-- =============================================================================
-- SECURITY DEFINER: DROP ONE DAY'S RAW CHUNK
-- =============================================================================

-- The exporter role holds only SELECT on sensor_measurements — never table ownership. drop_chunks
-- requires ownership, so the drop goes through this SECURITY DEFINER function, owned by the DB
-- superuser that owns the tenant tables. It validates its inputs (schema shape + single-day
-- window) rather than trusting the caller, and pins search_path — the standard hardening for a
-- SECURITY DEFINER function.
CREATE OR REPLACE FUNCTION cold_export_drop_day(
    p_schema TEXT,
    p_start  TIMESTAMPTZ,
    p_end    TIMESTAMPTZ
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF p_schema !~ '^tenant_[0-9a-f_]+$' THEN
        RAISE EXCEPTION 'cold_export_drop_day: invalid schema name %', p_schema;
    END IF;
    -- Bound the blast radius to exactly one day, matching the exporter's per-day unit.
    IF p_end <= p_start OR (p_end - p_start) > INTERVAL '1 day' THEN
        RAISE EXCEPTION 'cold_export_drop_day: window must be a single day, got % .. %', p_start, p_end;
    END IF;
    -- drop_chunks removes only chunks entirely within (newer_than, older_than). A chunk that
    -- overlaps a still-live boundary is left in place, so an un-exported day can never be dropped
    -- — the ordering guarantee holds even if chunk boundaries are not perfectly day-aligned.
    -- drop_chunks must be schema-qualified: this function pins a hardened search_path that
    -- excludes public (where TimescaleDB installs drop_chunks), so an unqualified call resolves
    -- to nothing. Positional args (relation, older_than, newer_than) — the "any"-typed
    -- older_than/newer_than don't resolve via named notation. The day window [p_start, p_end) is
    -- (p_end as older_than, p_start as newer_than). The table name is already schema-qualified, so
    -- its ::regclass resolves regardless of search_path.
    PERFORM public.drop_chunks(
        format('%I.sensor_measurements', p_schema)::regclass,
        p_end,
        p_start
    );
END;
$$;

COMMENT ON FUNCTION cold_export_drop_day IS
    'ADR-0025: drops one day''s raw sensor_measurements chunk (SECURITY DEFINER); called by cold_export only after a verified export';

-- SECURITY DEFINER hardening: no ambient EXECUTE. Grant only to the exporter role, if it exists.
REVOKE ALL ON FUNCTION cold_export_drop_day(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cold_export_user') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION cold_export_drop_day(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO cold_export_user';
    ELSE
        RAISE NOTICE 'cold_export_user does not exist yet — run 00-create-roles.sh, then re-run this migration to grant EXECUTE';
    END IF;
END $$;

-- =============================================================================
-- PER-SCHEMA: REMOVE BLIND RAW RETENTION + GRANT EXPORTER READ ACCESS
-- =============================================================================

CREATE OR REPLACE FUNCTION ensure_cold_export_access(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Drop any pre-ADR-0025 blind retention policy on raw. if_exists keeps this a no-op on
    -- schemas that never had one (e.g. created after 04 stopped adding it).
    PERFORM remove_retention_policy(format('%I.sensor_measurements', p_schema_name), if_exists => TRUE);

    -- Least-privilege read for the exporter: USAGE on the schema, SELECT on raw only.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cold_export_user') THEN
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO cold_export_user', p_schema_name);
        EXECUTE format('GRANT SELECT ON %I.sensor_measurements TO cold_export_user', p_schema_name);
    END IF;

    RAISE NOTICE '  ✓ Cold-export access ensured, blind raw retention removed for %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION ensure_cold_export_access IS
    'Idempotently removes the blind raw retention policy and grants cold_export_user SELECT on sensor_measurements (ADR-0025)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    processed INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Cold-Layer Raw Retention Migration (ADR-0025)';
    RAISE NOTICE '========================================';

    FOR schema_name IN
        SELECT nspname FROM pg_namespace WHERE nspname LIKE 'tenant_%' ORDER BY nspname
    LOOP
        PERFORM ensure_cold_export_access(schema_name);
        processed := processed + 1;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Tenant schemas processed: %', processed;
    RAISE NOTICE '  - Raw retention now owned by the cold-export job (export -> verify -> drop)';
    RAISE NOTICE '  - cold_export_user: SELECT on sensor_measurements + EXECUTE cold_export_drop_day';
    RAISE NOTICE '========================================';
END $$;
