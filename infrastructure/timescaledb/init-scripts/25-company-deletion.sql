-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Tenant deletion: the request, its record and the lifecycle record (ADR-0033 dec 4, 6, 7, 8)
-- Purpose: Delete is an explicit operator request, valid only for a tenant suspended for at least
--          30 days (dec 7), carried out store by store by a dedicated job that records each removal
--          (dec 4). Every lifecycle action is recorded durably, outside every tenant's schema, so
--          the record outlives the tenant (dec 6); the registry entry stays as a tombstone (dec 8).
--          The gateway role reads these tables and writes them only through the SECURITY DEFINER
--          functions below. Idempotent: safe to run repeatedly, and on an existing database.

-- =============================================================================
-- REGISTRY: WHEN A TENANT WAS SUSPENDED, AND WHETHER IT IS DELETED (dec 7, 8)
-- =============================================================================

ALTER TABLE public.companies ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ;
ALTER TABLE public.companies ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- A tenant already suspended when this runs has no recorded start: its wait begins now, so no
-- suspension is ever counted as longer than it is known to be.
UPDATE public.companies SET suspended_at = now() WHERE NOT is_active AND suspended_at IS NULL;

COMMENT ON COLUMN public.companies.suspended_at IS
    'When the tenant was last suspended; NULL while active. Delete waits 30 days from it (ADR-0033 dec 7)';
COMMENT ON COLUMN public.companies.deleted_at IS
    'Set when every store has been emptied; the row stays as a tombstone (ADR-0033 dec 8)';

-- =============================================================================
-- LIFECYCLE RECORD: WHO DID WHAT TO WHICH TENANT AND WHEN (dec 6)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.company_lifecycle_events (
    event_id BIGSERIAL PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES public.companies(company_id),
    action TEXT NOT NULL CHECK (action IN (
        'create', 'rename', 'deactivate', 'reactivate', 'delete_requested', 'delete_completed')),
    actor_id UUID,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_company_lifecycle_events_company
    ON public.company_lifecycle_events (company_id, at);

COMMENT ON TABLE public.company_lifecycle_events IS
    'Append-only record of tenant lifecycle actions (ADR-0033 dec 6); written only through record_company_lifecycle()';

CREATE OR REPLACE FUNCTION public.record_company_lifecycle(
    p_company_id UUID, p_action TEXT, p_actor_id UUID, p_detail JSONB DEFAULT '{}'::jsonb)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    PERFORM public.company_provisioning_guard();
    INSERT INTO public.company_lifecycle_events (company_id, action, actor_id, detail)
    VALUES (p_company_id, p_action, p_actor_id, coalesce(p_detail, '{}'::jsonb))
    RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

COMMENT ON FUNCTION public.record_company_lifecycle(UUID, TEXT, UUID, JSONB) IS
    'Appends one lifecycle record (SECURITY DEFINER); the gateway calls it in the transaction of the action it records';

-- =============================================================================
-- DELETION: THE REQUEST AND WHAT HAS BEEN REMOVED (dec 4, 7)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.company_deletions (
    company_id UUID PRIMARY KEY REFERENCES public.companies(company_id),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by UUID NOT NULL,
    completed_at TIMESTAMPTZ
);

-- One row per store the job has emptied, with what it removed and, for a store that ages the
-- tenant out on its own schedule, until when it may still hold the tenant's data (dec 4).
CREATE TABLE IF NOT EXISTS public.company_deletion_steps (
    company_id UUID NOT NULL REFERENCES public.company_deletions(company_id),
    store TEXT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retained_until TIMESTAMPTZ,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (company_id, store)
);

COMMENT ON TABLE public.company_deletions IS
    'One row per requested tenant deletion (ADR-0033 dec 4, 7); written only through request_company_deletion()';
COMMENT ON TABLE public.company_deletion_steps IS
    'Each store a tenant deletion has emptied, and what it removed (ADR-0033 dec 4)';

-- The minimum suspension before a delete may be requested (ADR-0033 dec 7).
CREATE OR REPLACE FUNCTION public.company_deletion_wait()
RETURNS INTERVAL
LANGUAGE sql IMMUTABLE
AS $$ SELECT interval '30 days' $$;

-- Refusals carry their own SQLSTATE so the API can answer each precisely:
--   P0002 no_data_found       the company does not exist
--   55000 object_not_in_prerequisite_state  not suspended, or not suspended long enough
--   23505 unique_violation    a deletion is already requested
CREATE OR REPLACE FUNCTION public.request_company_deletion(p_company_id UUID, p_actor_id UUID)
RETURNS TABLE (company_id UUID, requested_at TIMESTAMPTZ, requested_by UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
    v_company public.companies%ROWTYPE;
BEGIN
    PERFORM public.company_provisioning_guard();
    IF p_actor_id IS NULL THEN
        RAISE EXCEPTION 'a deletion request needs its actor' USING ERRCODE = '22004';
    END IF;
    SELECT * INTO v_company FROM public.companies c WHERE c.company_id = p_company_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'company % does not exist', p_company_id USING ERRCODE = 'P0002';
    END IF;
    IF v_company.is_active OR v_company.suspended_at IS NULL THEN
        RAISE EXCEPTION 'company % is not suspended', p_company_id USING ERRCODE = '55000',
            HINT = 'not_suspended';
    END IF;
    IF v_company.suspended_at > now() - public.company_deletion_wait() THEN
        RAISE EXCEPTION 'company % has been suspended for less than %', p_company_id,
            public.company_deletion_wait() USING ERRCODE = '55000', HINT = 'suspended_too_recently';
    END IF;

    INSERT INTO public.company_deletions (company_id, requested_by) VALUES (p_company_id, p_actor_id);
    PERFORM public.record_company_lifecycle(p_company_id, 'delete_requested', p_actor_id);

    RETURN QUERY
        SELECT d.company_id, d.requested_at, d.requested_by
        FROM public.company_deletions d WHERE d.company_id = p_company_id;
END;
$$;

COMMENT ON FUNCTION public.request_company_deletion(UUID, UUID) IS
    'Records an operator''s request to delete a tenant suspended for at least company_deletion_wait() (SECURITY DEFINER)';

-- =============================================================================
-- PRIVILEGES: the gateway reads the records and writes them only through the functions above.
-- =============================================================================

REVOKE ALL ON public.company_lifecycle_events, public.company_deletions, public.company_deletion_steps FROM PUBLIC;
GRANT SELECT ON public.company_lifecycle_events, public.company_deletions, public.company_deletion_steps
    TO api_gateway_user;

REVOKE ALL ON FUNCTION public.record_company_lifecycle(UUID, TEXT, UUID, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.request_company_deletion(UUID, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_company_lifecycle(UUID, TEXT, UUID, JSONB) TO api_gateway_user;
GRANT EXECUTE ON FUNCTION public.request_company_deletion(UUID, UUID) TO api_gateway_user;

DO $$
BEGIN
    RAISE NOTICE '✓ Tenant deletion: suspended_at/deleted_at, company_lifecycle_events, company_deletions, company_deletion_steps';
END
$$;
