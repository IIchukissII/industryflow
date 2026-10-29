-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Capability-Use Record (ADR-0042 dec 4, 6)
-- Purpose: The durable record of every capability's life: minted, first used by each plane,
--          refused, revoked or consumed, and the environments and SQL sessions it served.
--
--          The planes append events to a stream in the capability store; the API gateway's drain
--          is the one writer of this table, through record_capability_use(). The record sits
--          outside every tenant schema with no reference to the tenant registry, so it outlives a
--          deleted tenant (ADR-0033 dec 6), and it is backed up with the database (ADR-0032).
--          The column values are checked by the event contract that produced them
--          (libs/industryflow_contracts/industryflow_contracts/capability_audit.py); the checks
--          here repeat its closed lists so a writer that skipped it still cannot widen them.

CREATE TABLE IF NOT EXISTS public.capability_use_events (
    event_id BIGSERIAL PRIMARY KEY,
    -- The stream entry's id: the drain delivers at least once, and a redelivery is a no-op.
    stream_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN (
        'minted', 'first_use', 'refused', 'revoked', 'consumed', 'environment_started',
        'environment_stopped', 'sql_session_opened', 'sql_session_closed')),
    plane TEXT NOT NULL CHECK (plane IN (
        'hub', 'ml_service', 'api_gateway', 'sql_proxy', 'tracking_gateway', 'cold_broker',
        'tenant_delete')),
    at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    company_id UUID,
    user_id TEXT,
    audience TEXT CHECK (audience IN ('api', 'sql', 'tracking', 'cold', 'upload')),
    handle_id UUID,
    digest TEXT CHECK (digest ~ '^[0-9a-f]{16}$'),
    reason TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_capability_use_events_company
    ON public.capability_use_events (company_id, at);
CREATE INDEX IF NOT EXISTS idx_capability_use_events_handle
    ON public.capability_use_events (handle_id) WHERE handle_id IS NOT NULL;

COMMENT ON TABLE public.capability_use_events IS
    'Append-only record of capability use (ADR-0042); written only through record_capability_use()';

CREATE OR REPLACE FUNCTION public.record_capability_use(
    p_stream_id TEXT, p_kind TEXT, p_plane TEXT, p_at TIMESTAMPTZ, p_company_id UUID, p_user_id TEXT,
    p_audience TEXT, p_handle_id UUID, p_digest TEXT, p_reason TEXT, p_detail TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_id BIGINT;
BEGIN
    PERFORM public.company_provisioning_guard();
    INSERT INTO public.capability_use_events (
        stream_id, kind, plane, at, company_id, user_id, audience, handle_id, digest, reason, detail)
    VALUES (p_stream_id, p_kind, p_plane, p_at, p_company_id, p_user_id, p_audience, p_handle_id,
            p_digest, p_reason, p_detail)
    ON CONFLICT (stream_id) DO NOTHING
    RETURNING event_id INTO v_id;
    RETURN v_id IS NOT NULL;
END;
$$;

COMMENT ON FUNCTION public.record_capability_use(TEXT, TEXT, TEXT, TIMESTAMPTZ, UUID, TEXT, TEXT, UUID, TEXT, TEXT, TEXT) IS
    'Appends one capability-use event (SECURITY DEFINER); true when new, false for a redelivered stream entry';

-- Retention (ADR-0042 dec 10): rows older than the configured period leave the record, never sooner
-- than the floor below, whoever calls it. The floor outlasts the suspension a tenant's deletion waits
-- for (ADR-0033 dec 7), so a suspended tenant's record is complete when its deletion is decided.
CREATE OR REPLACE FUNCTION public.expire_capability_use(p_older_than_days INTEGER)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_removed BIGINT;
BEGIN
    PERFORM public.company_provisioning_guard();
    IF p_older_than_days IS NULL OR p_older_than_days < 30 THEN
        RAISE EXCEPTION 'the capability-use record is kept for at least 30 days, not %', p_older_than_days
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    DELETE FROM public.capability_use_events
    WHERE recorded_at < now() - make_interval(days => p_older_than_days);
    GET DIAGNOSTICS v_removed = ROW_COUNT;
    RETURN v_removed;
END;
$$;

COMMENT ON FUNCTION public.expire_capability_use(INTEGER) IS
    'Removes capability-use rows older than the period, never fewer than 30 days (SECURITY DEFINER)';

-- =============================================================================
-- PRIVILEGES: the gateway writes and expires only through the functions and reads for the record's
-- readers (dec 6). No role updates or deletes a row directly.
-- =============================================================================

REVOKE ALL ON public.capability_use_events FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.capability_use_events_event_id_seq FROM PUBLIC;
GRANT SELECT ON public.capability_use_events TO api_gateway_user;

REVOKE ALL ON FUNCTION public.record_capability_use(TEXT, TEXT, TEXT, TIMESTAMPTZ, UUID, TEXT, TEXT, UUID, TEXT, TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_capability_use(TEXT, TEXT, TEXT, TIMESTAMPTZ, UUID, TEXT, TEXT, UUID, TEXT, TEXT, TEXT)
    TO api_gateway_user;

REVOKE ALL ON FUNCTION public.expire_capability_use(INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.expire_capability_use(INTEGER) TO api_gateway_user;
