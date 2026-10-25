-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Cold-Layer Raw Retention Migration (ADR-0025)
-- Version: 1.1
-- Date: 2026-07-11 (1.1: 2026-10-13 — fingerprint-checked drop, drop record)
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
-- DROP RECORD: WHICH EXPORTED PART EACH COMMITTED DROP COMPLETED
-- =============================================================================

-- A day's export is listed in its object-store manifest BEFORE the day's rows are dropped, so a
-- crash can leave a listed part whose drop never committed. Whether it did is not something the
-- exporter may infer from row counts (late rows can make any two counts coincide); the database
-- records it here, in the same transaction as the drop. Written only by cold_export_drop_day.
CREATE TABLE IF NOT EXISTS public.cold_export_drops (
    schema_name TEXT        NOT NULL,
    day         DATE        NOT NULL,
    part        TEXT        NOT NULL,
    rows        BIGINT      NOT NULL,
    dropped_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (schema_name, day, part)
);

COMMENT ON TABLE public.cold_export_drops IS
    'ADR-0025: one row per committed cold-export drop, naming the Parquet part that completed the day';

REVOKE ALL ON public.cold_export_drops FROM PUBLIC;

-- =============================================================================
-- DAY FINGERPRINT: WHAT EXACTLY THE EXPORT READ
-- =============================================================================

-- The exporter reads a day in one REPEATABLE READ snapshot and takes this (row count, digest) in
-- the same snapshot; the drop re-takes it under a lock and drops only if it is unchanged. The
-- digest is a sum of per-row 64-bit hashes, so it is independent of row order and changes when any
-- row of the day is inserted, updated or deleted — a count alone cannot tell "one row left and
-- another arrived" from "nothing happened". Row-to-text depends on session settings, so they are
-- pinned here; both sides call this one function, so they compute the same thing.
CREATE OR REPLACE FUNCTION cold_export_day_fingerprint(
    p_schema TEXT,
    p_start  TIMESTAMPTZ,
    p_end    TIMESTAMPTZ,
    OUT rows        BIGINT,
    OUT fingerprint NUMERIC
)
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog, pg_temp
SET timezone = 'UTC'
SET datestyle = 'ISO, YMD'
SET extra_float_digits = 1
AS $$
BEGIN
    IF p_schema !~ '^tenant_[0-9a-f_]+$' THEN
        RAISE EXCEPTION 'cold_export_day_fingerprint: invalid schema name %', p_schema;
    END IF;
    EXECUTE format(
        'SELECT count(*), coalesce(sum(hashtextextended(m::text, 0)::numeric), 0) '
        'FROM %I.sensor_measurements m WHERE m.time >= $1 AND m.time < $2', p_schema)
        INTO rows, fingerprint USING p_start, p_end;
END;
$$;

COMMENT ON FUNCTION cold_export_day_fingerprint IS
    'ADR-0025: row count and order-independent digest of one day of raw sensor_measurements, taken by the exporter''s snapshot and re-taken by cold_export_drop_day';

-- =============================================================================
-- SECURITY DEFINER: DROP ONE DAY'S RAW CHUNK
-- =============================================================================

-- The exporter role holds only SELECT on sensor_measurements — never table ownership. drop_chunks
-- requires ownership, so the drop goes through this SECURITY DEFINER function, owned by the DB
-- superuser that owns the tenant tables. It validates its inputs (schema shape, single-day window,
-- part name) rather than trusting the caller, and pins search_path — the standard hardening for a
-- SECURITY DEFINER function.
--
-- It drops only the rows the export holds. It SHARE-locks every chunk of the day (blocking inserts
-- into and deletes from that day only — not reads, not writes to any other day), re-takes the
-- day's fingerprint, and returns false without dropping if it differs from the exported snapshot's:
-- a row that arrived while the export ran is then still in the source, and the next run exports it.
-- Otherwise it drops, checks the day is now empty, and records the drop, so the drop and its record
-- commit together or not at all. It must run READ COMMITTED: each statement then sees every row
-- committed before it, including rows committed before the lock was granted.
--
-- The earlier signature dropped without any check; it is removed so nothing can still call it.
DROP FUNCTION IF EXISTS cold_export_drop_day(TEXT, TIMESTAMPTZ, TIMESTAMPTZ);

CREATE OR REPLACE FUNCTION cold_export_drop_day(
    p_schema               TEXT,
    p_start                TIMESTAMPTZ,
    p_end                  TIMESTAMPTZ,
    p_expected_rows        BIGINT,
    p_expected_fingerprint NUMERIC,
    p_part                 TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_chunk       REGCLASS;
    v_rows        BIGINT;
    v_fingerprint NUMERIC;
BEGIN
    IF p_schema !~ '^tenant_[0-9a-f_]+$' THEN
        RAISE EXCEPTION 'cold_export_drop_day: invalid schema name %', p_schema;
    END IF;
    -- Bound the blast radius to exactly one day, matching the exporter's per-day unit.
    IF p_end <= p_start OR (p_end - p_start) > INTERVAL '1 day' THEN
        RAISE EXCEPTION 'cold_export_drop_day: window must be a single day, got % .. %', p_start, p_end;
    END IF;
    IF p_part IS NULL OR p_part !~ '^measurements(-[0-9]{4})?\.parquet$' THEN
        RAISE EXCEPTION 'cold_export_drop_day: invalid part name %', p_part;
    END IF;
    IF p_expected_rows IS NULL OR p_expected_rows < 0 OR p_expected_fingerprint IS NULL THEN
        RAISE EXCEPTION 'cold_export_drop_day: invalid expected rows/fingerprint % / %',
            p_expected_rows, p_expected_fingerprint;
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'cold_export_drop_day: must run READ COMMITTED, not %',
            current_setting('transaction_isolation');
    END IF;

    -- Every chunk overlapping the day, in a fixed (OID) order so two lockers cannot deadlock.
    FOR v_chunk IN
        SELECT format('%I.%I', c.chunk_schema, c.chunk_name)::regclass
        FROM timescaledb_information.chunks c
        WHERE c.hypertable_schema = p_schema
          AND c.hypertable_name = 'sensor_measurements'
          AND c.range_start < p_end
          AND c.range_end > p_start
        ORDER BY 1
    LOOP
        EXECUTE format('LOCK TABLE %s IN SHARE MODE', v_chunk);
    END LOOP;

    SELECT f.rows, f.fingerprint INTO v_rows, v_fingerprint
    FROM public.cold_export_day_fingerprint(p_schema, p_start, p_end) f;
    IF v_rows <> p_expected_rows OR v_fingerprint <> p_expected_fingerprint THEN
        RAISE NOTICE 'cold_export_drop_day: %.sensor_measurements changed in % .. % since the export (% rows now, % exported); not dropping',
            p_schema, p_start, p_end, v_rows, p_expected_rows;
        RETURN FALSE;
    END IF;

    -- drop_chunks removes only chunks entirely within (newer_than, older_than). drop_chunks must be
    -- schema-qualified: this function pins a hardened search_path that excludes public (where
    -- TimescaleDB installs drop_chunks), so an unqualified call resolves to nothing. Positional args
    -- (relation, older_than, newer_than) — the "any"-typed older_than/newer_than don't resolve via
    -- named notation. The day window [p_start, p_end) is (p_end as older_than, p_start as
    -- newer_than). The table name is already schema-qualified, so its ::regclass resolves regardless
    -- of search_path.
    PERFORM public.drop_chunks(
        format('%I.sensor_measurements', p_schema)::regclass,
        p_end,
        p_start
    );

    -- A chunk straddling the day boundary is not dropped, which would leave exported rows behind to
    -- be exported again. Refuse instead: the raise rolls the whole drop back.
    EXECUTE format('SELECT count(*) FROM %I.sensor_measurements WHERE time >= $1 AND time < $2', p_schema)
        INTO v_rows USING p_start, p_end;
    IF v_rows <> 0 THEN
        RAISE EXCEPTION 'cold_export_drop_day: % row(s) of %.sensor_measurements remain in % .. % after the drop (chunks not aligned to the day); nothing dropped',
            v_rows, p_schema, p_start, p_end;
    END IF;

    INSERT INTO public.cold_export_drops (schema_name, day, part, rows)
    VALUES (p_schema, (p_start AT TIME ZONE 'UTC')::date, p_part, p_expected_rows)
    ON CONFLICT (schema_name, day, part) DO NOTHING;
    RETURN TRUE;
END;
$$;

COMMENT ON FUNCTION cold_export_drop_day IS
    'ADR-0025: drops one day''s raw sensor_measurements chunk only if it still holds exactly the exported rows, and records the drop (SECURITY DEFINER); called by cold_export only after a verified export';

-- No ambient EXECUTE (SECURITY DEFINER hardening). Grant only to the exporter role, if it exists.
REVOKE ALL ON FUNCTION cold_export_day_fingerprint(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC;
REVOKE ALL ON FUNCTION cold_export_drop_day(TEXT, TIMESTAMPTZ, TIMESTAMPTZ, BIGINT, NUMERIC, TEXT) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cold_export_user') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION cold_export_day_fingerprint(TEXT, TIMESTAMPTZ, TIMESTAMPTZ) TO cold_export_user';
        EXECUTE 'GRANT EXECUTE ON FUNCTION cold_export_drop_day(TEXT, TIMESTAMPTZ, TIMESTAMPTZ, BIGINT, NUMERIC, TEXT) TO cold_export_user';
        EXECUTE 'GRANT SELECT ON public.cold_export_drops TO cold_export_user';
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
    RAISE NOTICE '  - cold_export_user: SELECT on sensor_measurements + EXECUTE cold_export_day_fingerprint/cold_export_drop_day';
    RAISE NOTICE '========================================';
END $$;
