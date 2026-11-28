-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Tenant quota overrides (ADR-0049)
-- Purpose: A platform superuser overrides a cap's platform default for one tenant through the API
--          gateway, which writes only through set_tenant_quota(): it accepts a known cap and a
--          whole number, or NULL to return the tenant to the default, changes the override and
--          appends the change to public.tenant_quota_changes in one transaction (dec 2); where the
--          deployment derives the cap's capacity, every tenant's override of it together stays within
--          it (dec 14). The gateway
--          holds no write privilege on either table; the alert worker reads the overrides to carry
--          them on the tenant-status stream. The deletion job removes a tenant's overrides with its
--          database objects (26, dec 7); the record of changes stays, as the lifecycle record does.

-- =============================================================================
-- THE OVERRIDES (dec 2, 7)
-- =============================================================================

-- The closed list of caps (dec 1): its one home in the database, the same list as the contract's
-- (libs/industryflow_contracts/industryflow_contracts/quotas.py), which a test holds equal. A new cap is
-- added here; one no longer in the contract stays, as an override of it may.
CREATE TABLE IF NOT EXISTS public.tenant_quota_caps (cap TEXT PRIMARY KEY);
INSERT INTO public.tenant_quota_caps (cap) VALUES
    ('readings_per_second'),
    ('notebook_environments'),
    ('sql_sessions'),
    ('sql_statement_seconds'),
    ('sql_idle_transaction_seconds'),
    ('stored_database_gib'),
    ('stored_object_gib')
ON CONFLICT (cap) DO NOTHING;

COMMENT ON TABLE public.tenant_quota_caps IS
    'The caps a tenant quota override may name (ADR-0049 dec 1)';

CREATE TABLE IF NOT EXISTS public.tenant_quota_overrides (
    company_id UUID NOT NULL REFERENCES public.companies (company_id),
    cap TEXT NOT NULL REFERENCES public.tenant_quota_caps (cap),
    value INTEGER NOT NULL CHECK (value BETWEEN 1 AND 1000000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID NOT NULL,
    PRIMARY KEY (company_id, cap)
);

-- A database from before the list was a table checked the cap against a list of its own.
ALTER TABLE public.tenant_quota_overrides DROP CONSTRAINT IF EXISTS tenant_quota_overrides_cap_check;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'public.tenant_quota_overrides'::regclass
                     AND conname = 'tenant_quota_overrides_cap_fkey') THEN
        ALTER TABLE public.tenant_quota_overrides ADD CONSTRAINT tenant_quota_overrides_cap_fkey
            FOREIGN KEY (cap) REFERENCES public.tenant_quota_caps (cap);
    END IF;
END
$$;

COMMENT ON TABLE public.tenant_quota_overrides IS
    'A tenant''s override of a cap''s platform default (ADR-0049 dec 2); written only through set_tenant_quota()';

-- =============================================================================
-- THE RECORD OF CHANGES (dec 2)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.tenant_quota_changes (
    change_id BIGSERIAL PRIMARY KEY,
    company_id UUID NOT NULL,
    cap TEXT NOT NULL,
    old_value INTEGER,
    new_value INTEGER,
    changed_by UUID NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tenant_quota_changes_company
    ON public.tenant_quota_changes (company_id, change_id);

COMMENT ON TABLE public.tenant_quota_changes IS
    'Append-only record of tenant quota overrides set and cleared (ADR-0049 dec 2); NULL is the platform default';

-- =============================================================================
-- THE ONE WRITER (dec 2)
-- =============================================================================

-- Sets (p_value a whole number) or clears (p_value NULL) a tenant's override. Returns whether it
-- changed and the override after the call; a call that changes nothing records nothing. p_capacity is
-- the cap's capacity the deployment derives (dec 14; NULL where it derives none): every tenant's
-- override of the cap together never exceeds it, counted under a lock per cap, so two calls cannot each
-- take the last part. An override that is lowered is never refused, so a capacity that shrank below
-- the overrides already set can be brought back under it. Refusals:
--   22004 null_value_not_allowed   no actor
--   22023 invalid_parameter_value  an unknown cap, or a value out of range
--   P0002 no_data_found            no such tenant, or one already deleted
--   53400 configuration_limit_exceeded  the overrides of the cap would exceed its capacity
-- The signature before the capacity: dropped, so no caller sets an override past it.
DROP FUNCTION IF EXISTS public.set_tenant_quota(UUID, TEXT, INTEGER, UUID);
CREATE OR REPLACE FUNCTION public.set_tenant_quota(p_company_id UUID, p_cap TEXT, p_value INTEGER, p_actor_id UUID,
                                                   p_capacity INTEGER)
RETURNS TABLE (changed BOOLEAN, value INTEGER, updated_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_current INTEGER;
    v_others BIGINT;
BEGIN
    PERFORM public.company_provisioning_guard();
    IF p_actor_id IS NULL THEN
        RAISE EXCEPTION 'a quota change needs its actor' USING ERRCODE = '22004';
    END IF;
    IF p_cap IS NULL OR NOT EXISTS (SELECT 1 FROM public.tenant_quota_caps k WHERE k.cap = p_cap) THEN
        RAISE EXCEPTION 'unknown tenant quota %', p_cap USING ERRCODE = '22023';
    END IF;
    IF p_value IS NOT NULL AND p_value NOT BETWEEN 1 AND 1000000 THEN
        RAISE EXCEPTION 'a tenant quota is a whole number from 1 to 1000000' USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM public.companies c
    WHERE c.company_id = p_company_id AND c.deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'company % does not exist', p_company_id USING ERRCODE = 'P0002';
    END IF;

    IF p_capacity IS NOT NULL THEN
        -- One writer of the cap's overrides at a time, so their sum is counted against what is committed.
        PERFORM pg_advisory_xact_lock(hashtext('tenant_quota_capacity'), hashtext(p_cap));
    END IF;
    SELECT o.value INTO v_current FROM public.tenant_quota_overrides o
    WHERE o.company_id = p_company_id AND o.cap = p_cap FOR UPDATE;
    IF v_current IS NOT DISTINCT FROM p_value THEN
        RETURN QUERY SELECT false, v_current, now();
        RETURN;
    END IF;
    IF p_capacity IS NOT NULL AND p_value IS NOT NULL AND p_value > coalesce(v_current, 0) THEN
        SELECT coalesce(sum(o.value), 0) INTO v_others FROM public.tenant_quota_overrides o
        WHERE o.cap = p_cap AND o.company_id <> p_company_id;
        IF v_others + p_value > p_capacity THEN
            RAISE EXCEPTION 'the overrides of % would exceed its capacity of %: the other tenants'' hold %, % is left',
                p_cap, p_capacity, v_others, greatest(p_capacity - v_others, 0)
                USING ERRCODE = '53400';
        END IF;
    END IF;

    IF p_value IS NULL THEN
        DELETE FROM public.tenant_quota_overrides o WHERE o.company_id = p_company_id AND o.cap = p_cap;
    ELSE
        INSERT INTO public.tenant_quota_overrides (company_id, cap, value, updated_by)
        VALUES (p_company_id, p_cap, p_value, p_actor_id)
        ON CONFLICT (company_id, cap)
        DO UPDATE SET value = EXCLUDED.value, updated_at = now(), updated_by = EXCLUDED.updated_by;
    END IF;
    INSERT INTO public.tenant_quota_changes (company_id, cap, old_value, new_value, changed_by)
    VALUES (p_company_id, p_cap, v_current, p_value, p_actor_id);
    RETURN QUERY SELECT true, p_value, now();
END;
$$;

COMMENT ON FUNCTION public.set_tenant_quota(UUID, TEXT, INTEGER, UUID, INTEGER) IS
    'Sets or clears a tenant''s quota override and records the change (SECURITY DEFINER, ADR-0049 dec 2)';

-- =============================================================================
-- STORED DATA (dec 11-13): each tenant's bytes in each shared store, measured in the store itself
-- =============================================================================

-- The last measurement of each tenant's data in each store. Derivable (dec 7): the next run of the
-- measurement job writes it again. The deletion job removes a tenant's rows with its database objects.
CREATE TABLE IF NOT EXISTS public.tenant_storage_usage (
    company_id UUID NOT NULL REFERENCES public.companies (company_id),
    store TEXT NOT NULL CHECK (store IN ('database', 'object')),
    bytes BIGINT NOT NULL CHECK (bytes >= 0),
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, store)
);

COMMENT ON TABLE public.tenant_storage_usage IS
    'Each tenant''s data in each shared store, as last measured (ADR-0049 dec 11); written only through record_tenant_storage_usage()';

-- Every tenant not deleted, with the bytes its schema holds in the database: its tables, their indexes
-- and TOAST, and for a hypertable every chunk, compressed or not, which live outside the schema. Read
-- from the database itself, never tallied by the writers (dec 11).
CREATE OR REPLACE FUNCTION public.tenant_database_bytes()
RETURNS TABLE (company_id UUID, bytes BIGINT)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    SELECT c.company_id,
           COALESCE(SUM(CASE WHEN h.hypertable_name IS NOT NULL
                             THEN public.hypertable_size(format('%I.%I', n.nspname, t.relname)::regclass)
                             ELSE pg_total_relation_size(t.oid) END), 0)::BIGINT
    FROM public.companies c
    LEFT JOIN pg_namespace n ON n.nspname = c.schema_name
    LEFT JOIN pg_class t ON t.relnamespace = n.oid AND t.relkind IN ('r', 'm')
    LEFT JOIN timescaledb_information.hypertables h
           ON h.hypertable_schema = n.nspname AND h.hypertable_name = t.relname
    WHERE c.deleted_at IS NULL
    GROUP BY c.company_id;
$$;

COMMENT ON FUNCTION public.tenant_database_bytes() IS
    'Every tenant not deleted and the bytes its schema holds, chunks included (SECURITY DEFINER, ADR-0049 dec 11)';

-- Records one measurement. A tenant deleted since it was measured is skipped, so a run that overlaps a
-- deletion leaves no row behind it. Refusals: 22023 an unknown store or a negative size.
CREATE OR REPLACE FUNCTION public.record_tenant_storage_usage(p_company_id UUID, p_store TEXT, p_bytes BIGINT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF p_store IS NULL OR p_store NOT IN ('database', 'object') THEN
        RAISE EXCEPTION 'unknown store %', p_store USING ERRCODE = '22023';
    END IF;
    IF p_bytes IS NULL OR p_bytes < 0 THEN
        RAISE EXCEPTION 'a stored size is a whole number of bytes' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.tenant_storage_usage (company_id, store, bytes, measured_at)
    SELECT c.company_id, p_store, p_bytes, now()
    FROM public.companies c
    WHERE c.company_id = p_company_id AND c.deleted_at IS NULL
    ON CONFLICT (company_id, store) DO UPDATE SET bytes = EXCLUDED.bytes, measured_at = EXCLUDED.measured_at;
END;
$$;

COMMENT ON FUNCTION public.record_tenant_storage_usage(UUID, TEXT, BIGINT) IS
    'Records a tenant''s measured bytes in one store (SECURITY DEFINER, ADR-0049 dec 11)';

-- =============================================================================
-- PRIVILEGES: the gateway changes overrides only through the function; the alert worker reads them
-- =============================================================================

REVOKE ALL ON public.tenant_quota_caps, public.tenant_quota_overrides, public.tenant_quota_changes FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.tenant_quota_changes_change_id_seq FROM PUBLIC;
GRANT SELECT ON public.tenant_quota_overrides, public.tenant_quota_changes TO api_gateway_user;
GRANT SELECT (company_id, cap, value) ON public.tenant_quota_overrides TO alert_service_user;

-- The measurement job measures and records only through the two functions; the alert worker reads the
-- measurements to refuse a tenant over its stored-data caps, the gateway to show them to a superuser.
REVOKE ALL ON public.tenant_storage_usage FROM PUBLIC;
GRANT SELECT ON public.tenant_storage_usage TO api_gateway_user, alert_service_user;
REVOKE ALL ON FUNCTION public.tenant_database_bytes() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_tenant_storage_usage(UUID, TEXT, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.tenant_database_bytes() TO tenant_usage_user;
GRANT EXECUTE ON FUNCTION public.record_tenant_storage_usage(UUID, TEXT, BIGINT) TO tenant_usage_user;

REVOKE ALL ON FUNCTION public.set_tenant_quota(UUID, TEXT, INTEGER, UUID, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.set_tenant_quota(UUID, TEXT, INTEGER, UUID, INTEGER) TO api_gateway_user;

DO $$
BEGIN
    RAISE NOTICE '✓ Tenant quotas: tenant_quota_overrides, tenant_quota_changes, set_tenant_quota(), tenant_storage_usage';
END
$$;
