-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- The notebook hub's window onto the deletion record (ADR-0020 dec 6, ADR-0033 dec 4)
-- Purpose: The hub reclaims a deleted tenant's notebook volumes itself and records that it did. It
--          reads which users await a reclaim and records the notebook_volumes store, and nothing
--          else: the store name is fixed here, so the hub cannot record any other store or empty
--          the database. notebook_hub_reclaim_user holds no table privilege. Idempotent: safe to
--          run repeatedly.

-- =============================================================================
-- WHO AWAITS A RECLAIM
-- =============================================================================

-- Every pending deletion whose volumes are not yet recorded, with the tenant's users (the hub's
-- user names). The user table is created at runtime by the gateway, so it may not exist yet.
CREATE OR REPLACE FUNCTION public.pending_notebook_volume_reclaims()
RETURNS TABLE (company_id UUID, user_ids UUID[])
LANGUAGE plpgsql STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_company UUID;
    v_users UUID[];
BEGIN
    FOR v_company IN
        SELECT d.company_id FROM public.company_deletions d
        WHERE d.completed_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM public.company_deletion_steps s
                          WHERE s.company_id = d.company_id AND s.store = 'notebook_volumes')
        ORDER BY d.requested_at
    LOOP
        v_users := '{}';
        IF to_regclass('public."user"') IS NOT NULL THEN
            EXECUTE 'SELECT coalesce(array_agg(id ORDER BY id), ''{}'') FROM public."user" WHERE company_id = $1'
                INTO v_users USING v_company;
        END IF;
        company_id := v_company;
        user_ids := v_users;
        RETURN NEXT;
    END LOOP;
END;
$$;

-- =============================================================================
-- RECORD THE RECLAIM: this one store only
-- =============================================================================

CREATE OR REPLACE FUNCTION public.record_notebook_volumes_reclaimed(p_company_id UUID, p_detail JSONB)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$ SELECT public.record_company_deletion_step(p_company_id, 'notebook_volumes', NULL, p_detail) $$;

-- =============================================================================
-- PRIVILEGES: the hub acts only through these functions.
-- =============================================================================

REVOKE ALL ON FUNCTION public.pending_notebook_volume_reclaims() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.record_notebook_volumes_reclaimed(UUID, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pending_notebook_volume_reclaims() TO notebook_hub_reclaim_user;
GRANT EXECUTE ON FUNCTION public.record_notebook_volumes_reclaimed(UUID, JSONB) TO notebook_hub_reclaim_user;

DO $$
BEGIN
    RAISE NOTICE '✓ Notebook volume reclaim interface: pending reclaims, reclaim record';
END
$$;
