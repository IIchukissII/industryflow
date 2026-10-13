-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Company (tenant) provisioning through the API
-- Purpose: give the API gateway a narrow, audited write path to the tenant registry. The
--          gateway role holds only SELECT on public.companies and cannot create schemas or
--          roles, so it provisions a tenant by calling these SECURITY DEFINER functions, which
--          validate their input, write the registry row and build the tenant schema with the
--          existing create_tenant_schema(). Who may call them (superuser vs tenant admin) is
--          decided by the API (services/api_gateway/routers/companies.py).
--          Idempotent: safe to run repeatedly, and on an existing database (psql -f).
--
-- There is no teardown: a company is deactivated, never dropped. Its schema, data and reader role
-- stay; deleting tenant data is an operator action, not an API call.

-- =============================================================================
-- GUARD: NOTHING UNTRUSTED ON THE DEFINER'S SEARCH PATH
-- =============================================================================

-- create_tenant_schema() and its helpers call functions unqualified (create_base_tables,
-- create_hypertable, ...), so the definer functions below must put public on their search path.
-- api_gateway_user has CREATE on public (it creates its user table at runtime), so it could plant
-- a better-matching overload there that would then run with the definer's rights. pg_catalog
-- comes first (built-ins cannot be shadowed), and before doing anything the definer refuses to
-- run while public holds any function or operator owned by a role it cannot trust: neither a
-- superuser nor the definer's own owner.
CREATE OR REPLACE FUNCTION public.company_provisioning_guard()
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_owner oid := (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user);
    v_untrusted TEXT;
BEGIN
    SELECT string_agg(name, ', ') INTO v_untrusted FROM (
        SELECT p.oid::regprocedure::text AS name
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_roles r ON r.oid = p.proowner
        WHERE p.pronamespace = 'public'::regnamespace AND NOT r.rolsuper AND p.proowner <> v_owner
        UNION ALL
        SELECT o.oid::regoperator::text
        FROM pg_catalog.pg_operator o JOIN pg_catalog.pg_roles r ON r.oid = o.oprowner
        WHERE o.oprnamespace = 'public'::regnamespace AND NOT r.rolsuper AND o.oprowner <> v_owner
    ) untrusted;
    IF v_untrusted IS NOT NULL THEN
        RAISE EXCEPTION 'company provisioning refused: schema public holds objects of an untrusted owner: %', v_untrusted
            USING ERRCODE = 'insufficient_privilege';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION public.company_provisioning_guard() FROM PUBLIC;

-- =============================================================================
-- INPUT VALIDATION
-- =============================================================================

CREATE OR REPLACE FUNCTION public.company_name_checked(p_name TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_name TEXT := btrim(p_name);
BEGIN
    IF v_name IS NULL OR v_name = '' OR length(v_name) > 255 OR v_name ~ '[[:cntrl:]]' THEN
        RAISE EXCEPTION 'company name must be 1-255 characters with no control characters'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN v_name;
END;
$$;

REVOKE ALL ON FUNCTION public.company_name_checked(TEXT) FROM PUBLIC;

-- =============================================================================
-- PROVISION: registry row + tenant schema, in one transaction
-- =============================================================================

CREATE OR REPLACE FUNCTION public.provision_company(p_company_name TEXT, p_is_active BOOLEAN DEFAULT TRUE)
RETURNS TABLE (company_id UUID, company_name VARCHAR, is_active BOOLEAN, created_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_id UUID := gen_random_uuid();
    v_name TEXT;
BEGIN
    PERFORM public.company_provisioning_guard();
    v_name := public.company_name_checked(p_company_name);

    -- schema_name is the one canonical company_id -> schema mapping (ADR-0003 dec 5), the same
    -- expression create_tenant_schema() uses. A duplicate name raises unique_violation.
    INSERT INTO public.companies (company_id, company_name, schema_name, is_active)
    VALUES (v_id, v_name, 'tenant_' || replace(v_id::text, '-', '_'), coalesce(p_is_active, TRUE));

    PERFORM public.create_tenant_schema(v_id, v_name::VARCHAR);

    RETURN QUERY
        SELECT c.company_id, c.company_name, c.is_active, c.created_at
        FROM public.companies c WHERE c.company_id = v_id;
END;
$$;

COMMENT ON FUNCTION public.provision_company(TEXT, BOOLEAN) IS
    'Creates a company and its tenant schema (SECURITY DEFINER); called by the API gateway for a superuser';

-- =============================================================================
-- UPDATE: rename and (de)activate. NULL leaves a field unchanged.
-- =============================================================================

CREATE OR REPLACE FUNCTION public.update_company(p_company_id UUID, p_company_name TEXT, p_is_active BOOLEAN)
RETURNS TABLE (company_id UUID, company_name VARCHAR, is_active BOOLEAN, created_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_name TEXT;
    v_found BOOLEAN;
BEGIN
    PERFORM public.company_provisioning_guard();
    IF p_company_name IS NOT NULL THEN
        v_name := public.company_name_checked(p_company_name);
    END IF;

    UPDATE public.companies c
    SET company_name = coalesce(v_name, c.company_name),
        is_active = coalesce(p_is_active, c.is_active)
    WHERE c.company_id = p_company_id;
    v_found := FOUND;

    -- Suspend / resume the tenant's database identity with the flag (ADR-0033 dec 3): its SQL
    -- login is NOLOGIN with no open session while inactive, LOGIN again once reactivated. The
    -- reader role is NOLOGIN either way, so the SQL login is the tenant's only login identity.
    -- A registry row without a provisioned tenant has no identity to change.
    IF p_is_active IS NOT NULL AND v_found AND EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'tenant_reader_' || replace(p_company_id::text, '-', '_')
    ) THEN
        PERFORM public.ensure_tenant_sql_login(p_company_id);
    END IF;

    RETURN QUERY
        SELECT c.company_id, c.company_name, c.is_active, c.created_at
        FROM public.companies c WHERE c.company_id = p_company_id;
END;
$$;

COMMENT ON FUNCTION public.update_company(UUID, TEXT, BOOLEAN) IS
    'Renames and/or (de)activates a company, suspending or resuming its SQL login (SECURITY DEFINER); no row = unknown company';

-- SECURITY DEFINER hardening: no ambient EXECUTE; only the gateway role may call them.
REVOKE ALL ON FUNCTION public.provision_company(TEXT, BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.update_company(UUID, TEXT, BOOLEAN) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.provision_company(TEXT, BOOLEAN) TO api_gateway_user;
GRANT EXECUTE ON FUNCTION public.update_company(UUID, TEXT, BOOLEAN) TO api_gateway_user;

DO $$
BEGIN
    RAISE NOTICE '✓ Company provisioning: provision_company(), update_company() (SECURITY DEFINER, api_gateway_user only)';
END $$;
