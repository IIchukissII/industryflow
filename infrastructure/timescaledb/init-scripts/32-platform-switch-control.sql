-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Platform switch control (ADR-0043)
-- Purpose: A platform superuser flips a platform switch (ADR-0024's stateful-feature switch) through
--          the API gateway, which writes only through set_platform_switch(): it accepts a known
--          switch and a value of its type, changes the row and appends the flip to
--          public.platform_config_changes in one transaction. The gateway holds no write privilege
--          on either table; ml_service_user keeps SELECT on the switch alone (17), so the service
--          that reads it can never trip it.

-- =============================================================================
-- THE RECORD OF FLIPS (dec 4)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.platform_config_changes (
    change_id BIGSERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB NOT NULL,
    changed_by UUID NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_platform_config_changes_key
    ON public.platform_config_changes (key, change_id);

COMMENT ON TABLE public.platform_config_changes IS
    'Append-only record of platform switch flips (ADR-0043 dec 4); written only through set_platform_switch()';

-- =============================================================================
-- THE ONE WRITER (dec 3, 5)
-- =============================================================================

-- Returns whether the switch changed, and its value and change time after the call. A value equal
-- to the current one changes and records nothing (dec 5).
CREATE OR REPLACE FUNCTION public.set_platform_switch(p_key TEXT, p_value JSONB, p_actor_id UUID)
RETURNS TABLE (changed BOOLEAN, value JSONB, updated_at TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_current JSONB;
    v_at TIMESTAMPTZ;
BEGIN
    PERFORM public.company_provisioning_guard();
    IF p_actor_id IS NULL THEN
        RAISE EXCEPTION 'a switch flip needs its actor' USING ERRCODE = '22004';
    END IF;
    -- The closed list of switches and their types: a new switch is added here (ADR-0043).
    IF p_key IS DISTINCT FROM 'stateful_features_enabled' THEN
        RAISE EXCEPTION 'unknown platform switch %', p_key USING ERRCODE = '22023';
    END IF;
    IF p_value IS NULL OR jsonb_typeof(p_value) <> 'boolean' THEN
        RAISE EXCEPTION 'platform switch % takes a boolean', p_key USING ERRCODE = '22023';
    END IF;

    SELECT c.value, c.updated_at INTO v_current, v_at
    FROM public.platform_config c WHERE c.key = p_key FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'platform switch % is not installed', p_key USING ERRCODE = '22023';
    END IF;
    IF v_current = p_value THEN
        RETURN QUERY SELECT false, v_current, v_at;
        RETURN;
    END IF;

    UPDATE public.platform_config c
    SET value = p_value, updated_at = now(), updated_by = p_actor_id::text
    WHERE c.key = p_key;
    INSERT INTO public.platform_config_changes (key, old_value, new_value, changed_by)
    VALUES (p_key, v_current, p_value, p_actor_id);
    RETURN QUERY SELECT true, p_value, now();
END;
$$;

COMMENT ON FUNCTION public.set_platform_switch(TEXT, JSONB, UUID) IS
    'Flips a known platform switch and records the flip (SECURITY DEFINER, ADR-0043)';

-- =============================================================================
-- PRIVILEGES: the gateway flips only through the function and reads for the operator (dec 2, 3)
-- =============================================================================

REVOKE ALL ON public.platform_config_changes FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.platform_config_changes_change_id_seq FROM PUBLIC;
GRANT SELECT ON public.platform_config_changes TO api_gateway_user;
GRANT SELECT ON public.platform_config TO api_gateway_user;

REVOKE ALL ON FUNCTION public.set_platform_switch(TEXT, JSONB, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.set_platform_switch(TEXT, JSONB, UUID) TO api_gateway_user;
