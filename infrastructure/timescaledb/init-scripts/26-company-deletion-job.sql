-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- The tenant-delete job's database interface (ADR-0033 dec 4, 8)
-- Purpose: The job removes a requested tenant from every store, one store at a time, and records
--          each removal so a crashed run resumes where it stopped. Which stores a deletion must
--          empty is defined here once; the job reads it. The database itself is emptied last, and
--          only once every other store is recorded: dropping the tenant's schema and identities
--          first would lose the one place that says what is still to be removed. The registry entry
--          stays as a tombstone. tenant_delete_user holds no table privilege; it acts only through
--          these SECURITY DEFINER functions. Idempotent: safe to run repeatedly.

-- =============================================================================
-- THE STORES A DELETION MUST EMPTY BEFORE THE DATABASE
-- =============================================================================

CREATE OR REPLACE FUNCTION public.company_deletion_required_stores()
RETURNS TEXT[]
LANGUAGE sql IMMUTABLE
AS $$ SELECT ARRAY[
    'capability_store',     -- capability handles, session indexes and feature snapshots
    'cold_layer',           -- raw history in the open columnar archive (ADR-0025)
    'tracking_store',       -- experiments, runs, registered models and their artifacts (ADR-0013)
    'notebook_volumes',     -- each user's durable work volume, reclaimed by the hub (ADR-0020 dec 6)
    'device_certificates',  -- every device certificate of the tenant, revoked (ADR-0007)
    'broker'                -- in-flight readings: aged out by retention, recorded with its end
]::TEXT[] $$;
-- Backups are not a step before the database: every dump taken until the schema is dropped still
-- holds the tenant, so complete_company_deletion() records them, aged out from its own moment.

-- =============================================================================
-- WHAT IS PENDING, AND FOR WHOM
-- =============================================================================

CREATE OR REPLACE FUNCTION public.pending_company_deletions()
RETURNS TABLE (company_id UUID, requested_at TIMESTAMPTZ, removed TEXT[])
LANGUAGE sql STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    SELECT d.company_id, d.requested_at,
           coalesce(array_agg(s.store ORDER BY s.store) FILTER (WHERE s.store IS NOT NULL), '{}')
    FROM public.company_deletions d
    LEFT JOIN public.company_deletion_steps s ON s.company_id = d.company_id
    WHERE d.completed_at IS NULL
    GROUP BY d.company_id, d.requested_at
    ORDER BY d.requested_at
$$;

-- The tenant's users, whose sessions, handles and volumes the job removes. The user table is
-- created at runtime by the gateway, so it may not exist yet.
CREATE OR REPLACE FUNCTION public.company_deletion_user_ids(p_company_id UUID)
RETURNS SETOF UUID
LANGUAGE plpgsql STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.company_deletions d
                   WHERE d.company_id = p_company_id AND d.completed_at IS NULL) THEN
        RETURN;
    END IF;
    IF to_regclass('public."user"') IS NULL THEN
        RETURN;
    END IF;
    RETURN QUERY EXECUTE 'SELECT id FROM public."user" WHERE company_id = $1' USING p_company_id;
END;
$$;

-- =============================================================================
-- RECORD ONE STORE'S REMOVAL
-- =============================================================================

CREATE OR REPLACE FUNCTION public.record_company_deletion_step(
    p_company_id UUID, p_store TEXT, p_retained_until TIMESTAMPTZ DEFAULT NULL,
    p_detail JSONB DEFAULT '{}'::jsonb)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    PERFORM public.company_provisioning_guard();
    IF NOT p_store = ANY (public.company_deletion_required_stores()) THEN
        RAISE EXCEPTION 'unknown store %', p_store USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.company_deletions d
                   WHERE d.company_id = p_company_id AND d.completed_at IS NULL) THEN
        RAISE EXCEPTION 'company % has no pending deletion', p_company_id USING ERRCODE = 'P0002';
    END IF;
    -- The first record of a store stands: a re-run after a crash records nothing new.
    INSERT INTO public.company_deletion_steps (company_id, store, retained_until, detail)
    VALUES (p_company_id, p_store, p_retained_until, coalesce(p_detail, '{}'::jsonb))
    ON CONFLICT (company_id, store) DO NOTHING;
    RETURN FOUND;
END;
$$;

-- =============================================================================
-- EMPTY THE DATABASE, LAST, AND LEAVE THE TOMBSTONE (dec 4, 8)
-- =============================================================================

CREATE OR REPLACE FUNCTION public.complete_company_deletion(p_company_id UUID, p_backup_retention INTERVAL)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_suffix TEXT := replace(p_company_id::text, '-', '_');
    v_schema TEXT;
    v_reader TEXT := 'tenant_reader_' || v_suffix;
    v_login TEXT := 'tenant_sqlproxy_' || v_suffix;
    v_missing TEXT[];
    v_users INTEGER := 0;
    v_drops INTEGER := 0;
    v_detail JSONB;
BEGIN
    PERFORM public.company_provisioning_guard();
    IF p_backup_retention IS NULL OR p_backup_retention <= interval '0' THEN
        RAISE EXCEPTION 'the backup retention must be given' USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM public.company_deletions d WHERE d.company_id = p_company_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'company % has no deletion request', p_company_id USING ERRCODE = 'P0002';
    END IF;
    IF EXISTS (SELECT 1 FROM public.company_deletions d
               WHERE d.company_id = p_company_id AND d.completed_at IS NOT NULL) THEN
        RETURN (SELECT s.detail FROM public.company_deletion_steps s
                WHERE s.company_id = p_company_id AND s.store = 'database');
    END IF;

    SELECT array_agg(r ORDER BY r) INTO v_missing
    FROM unnest(public.company_deletion_required_stores()) r
    WHERE NOT EXISTS (SELECT 1 FROM public.company_deletion_steps s
                      WHERE s.company_id = p_company_id AND s.store = r);
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'company % still has data in %', p_company_id, v_missing
            USING ERRCODE = '55000', HINT = 'stores_pending';
    END IF;

    SELECT c.schema_name INTO v_schema FROM public.companies c WHERE c.company_id = p_company_id;
    IF v_schema IS DISTINCT FROM 'tenant_' || replace(p_company_id::text, '-', '_') THEN
        RAISE EXCEPTION 'company % does not map to its canonical schema', p_company_id USING ERRCODE = '22023';
    END IF;

    -- The tenant's login first: no session of it survives the drop.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = v_login) THEN
        EXECUTE format('ALTER ROLE %I NOLOGIN', v_login);
        PERFORM pg_terminate_backend(a.pid) FROM pg_catalog.pg_stat_activity a
        WHERE a.usename = v_login AND a.pid <> pg_backend_pid();
    END IF;

    EXECUTE format('DROP SCHEMA IF EXISTS %I CASCADE', v_schema);
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = v_login) THEN
        EXECUTE format('DROP OWNED BY %I', v_login);
        EXECUTE format('DROP ROLE %I', v_login);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = v_reader) THEN
        EXECUTE format('DROP OWNED BY %I', v_reader);
        EXECUTE format('DROP ROLE %I', v_reader);
    END IF;

    IF to_regclass('public."user"') IS NOT NULL THEN
        EXECUTE 'DELETE FROM public."user" WHERE company_id = $1' USING p_company_id;
        GET DIAGNOSTICS v_users = ROW_COUNT;
    END IF;
    IF to_regclass('public.cold_export_drops') IS NOT NULL THEN
        DELETE FROM public.cold_export_drops e WHERE e.schema_name = v_schema;
        GET DIAGNOSTICS v_drops = ROW_COUNT;
    END IF;

    v_detail := jsonb_build_object('schema', v_schema, 'roles', jsonb_build_array(v_login, v_reader),
                                   'users', v_users, 'cold_export_drops', v_drops);
    INSERT INTO public.company_deletion_steps (company_id, store, detail)
    VALUES (p_company_id, 'database', v_detail)
    ON CONFLICT (company_id, store) DO NOTHING;
    -- The last dump that holds the tenant is taken before now; the cold copy drops objects one
    -- retention period after the cold layer did (ADR-0032 dec 4).
    INSERT INTO public.company_deletion_steps (company_id, store, retained_until, detail)
    VALUES (p_company_id, 'backups', now() + p_backup_retention,
            jsonb_build_object('retention', p_backup_retention::text))
    ON CONFLICT (company_id, store) DO NOTHING;

    UPDATE public.companies c SET is_active = FALSE, deleted_at = now() WHERE c.company_id = p_company_id;
    UPDATE public.company_deletions d SET completed_at = now() WHERE d.company_id = p_company_id;
    INSERT INTO public.company_lifecycle_events (company_id, action, actor_id, detail)
    VALUES (p_company_id, 'delete_completed', NULL, jsonb_build_object('by', 'tenant-delete job'));
    RETURN v_detail;
END;
$$;

COMMENT ON FUNCTION public.complete_company_deletion(UUID, INTERVAL) IS
    'Drops a tenant''s database objects once every other store is recorded, and leaves the tombstone (SECURITY DEFINER)';

-- =============================================================================
-- PRIVILEGES: the job acts only through these functions.
-- =============================================================================

REVOKE ALL ON FUNCTION public.pending_company_deletions() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.company_deletion_user_ids(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_company_deletion_step(UUID, TEXT, TIMESTAMPTZ, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete_company_deletion(UUID, INTERVAL) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.company_deletion_required_stores() TO tenant_delete_user;
GRANT EXECUTE ON FUNCTION public.pending_company_deletions() TO tenant_delete_user;
GRANT EXECUTE ON FUNCTION public.company_deletion_user_ids(UUID) TO tenant_delete_user;
GRANT EXECUTE ON FUNCTION public.record_company_deletion_step(UUID, TEXT, TIMESTAMPTZ, JSONB) TO tenant_delete_user;
GRANT EXECUTE ON FUNCTION public.complete_company_deletion(UUID, INTERVAL) TO tenant_delete_user;

DO $$
BEGIN
    RAISE NOTICE '✓ Tenant-delete job interface: required stores, pending deletions, step record, completion';
END
$$;
