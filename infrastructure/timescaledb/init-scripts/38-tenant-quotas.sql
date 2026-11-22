-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Tenant quota overrides (ADR-0049)
-- Purpose: A platform superuser overrides a cap's platform default for one tenant through the API
--          gateway, which writes only through set_tenant_quota(): it accepts a known cap and a
--          whole number, or NULL to return the tenant to the default, changes the override and
--          appends the change to public.tenant_quota_changes in one transaction (dec 2). The gateway
--          holds no write privilege on either table; the alert worker reads the overrides to carry
--          them on the tenant-status stream. The deletion job removes a tenant's overrides with its
--          database objects (26, dec 7); the record of changes stays, as the lifecycle record does.

-- =============================================================================
-- THE OVERRIDES (dec 2, 7)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.tenant_quota_overrides (
    company_id UUID NOT NULL REFERENCES public.companies (company_id),
    cap TEXT NOT NULL CHECK (cap IN ('readings_per_second', 'notebook_environments')),
    value INTEGER NOT NULL CHECK (value BETWEEN 1 AND 1000000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by UUID NOT NULL,
    PRIMARY KEY (company_id, cap)
);

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
-- changed and the override after the call; a call that changes nothing records nothing. Refusals:
--   22004 null_value_not_allowed   no actor
--   22023 invalid_parameter_value  an unknown cap, or a value out of range
--   P0002 no_data_found            no such tenant, or one already deleted
CREATE OR REPLACE FUNCTION public.set_tenant_quota(p_company_id UUID, p_cap TEXT, p_value INTEGER, p_actor_id UUID)
RETURNS TABLE (changed BOOLEAN, value INTEGER, updated_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_current INTEGER;
BEGIN
    PERFORM public.company_provisioning_guard();
    IF p_actor_id IS NULL THEN
        RAISE EXCEPTION 'a quota change needs its actor' USING ERRCODE = '22004';
    END IF;
    -- The closed list of caps: a new cap is added here, in the table's check and in the contract
    -- (libs/industryflow_contracts/industryflow_contracts/quotas.py).
    IF p_cap IS NULL OR p_cap NOT IN ('readings_per_second', 'notebook_environments') THEN
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

    SELECT o.value INTO v_current FROM public.tenant_quota_overrides o
    WHERE o.company_id = p_company_id AND o.cap = p_cap FOR UPDATE;
    IF v_current IS NOT DISTINCT FROM p_value THEN
        RETURN QUERY SELECT false, v_current, now();
        RETURN;
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

COMMENT ON FUNCTION public.set_tenant_quota(UUID, TEXT, INTEGER, UUID) IS
    'Sets or clears a tenant''s quota override and records the change (SECURITY DEFINER, ADR-0049 dec 2)';

-- =============================================================================
-- PRIVILEGES: the gateway changes overrides only through the function; the alert worker reads them
-- =============================================================================

REVOKE ALL ON public.tenant_quota_overrides, public.tenant_quota_changes FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.tenant_quota_changes_change_id_seq FROM PUBLIC;
GRANT SELECT ON public.tenant_quota_overrides, public.tenant_quota_changes TO api_gateway_user;
GRANT SELECT (company_id, cap, value) ON public.tenant_quota_overrides TO alert_service_user;

REVOKE ALL ON FUNCTION public.set_tenant_quota(UUID, TEXT, INTEGER, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.set_tenant_quota(UUID, TEXT, INTEGER, UUID) TO api_gateway_user;

DO $$
BEGIN
    RAISE NOTICE '✓ Tenant quotas: tenant_quota_overrides, tenant_quota_changes, set_tenant_quota()';
END
$$;
