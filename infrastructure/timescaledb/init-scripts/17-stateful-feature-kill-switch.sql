-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Stateful-feature kill-switch (ADR-0024 rev 1)
-- Version: 1.0
-- Date: July 12, 2026
-- Purpose: The live, operator-flipped switch that neutralizes the stateful feature class when the
--          substrate it reads (the Spark-materialized aggregate tables) is degraded. Flipping it
--          off makes the engine fill each stateful feature's slot with its neutral value WITHOUT
--          calling the transform — so a degraded database stops being queried N times per
--          inference, which is the relief the switch exists to provide (ADR-0024 dec 4).
--
--          The flag is PLATFORM-GLOBAL, not tenant data, so it lives in the shared `public` schema
--          and never in a tenant schema (ADR-0003). Readers must qualify it as
--          `public.platform_config`: the inference path may hold a tenant `search_path`, under
--          which a bare `platform_config` would resolve into the tenant schema and fail.

-- =============================================================================
-- PLATFORM CONFIG (public schema — global operational flags)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.platform_config (
    key         TEXT PRIMARY KEY,
    value       JSONB       NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  TEXT
);

COMMENT ON TABLE public.platform_config IS
    'Platform-global operational flags (ADR-0024). Not tenant data — never replicated into a tenant schema.';

-- The switch itself. Enabled by default: the platform''s normal state is that stateful features
-- compute. It is turned off deliberately, by a human, during an incident (ADR-0024 dec 6-7) — the
-- service never trips it automatically, and never writes this row.
INSERT INTO public.platform_config (key, value, description, updated_by)
VALUES (
    'stateful_features_enabled',
    'true'::jsonb,
    'ADR-0024: when false, the feature engine fills every stateful transform''s slot with its neutral value and does NOT call the transform — relieving the aggregate-table reads on the inference hot path. Flip to false during a degraded-database incident; flip back when it recovers.',
    'migration:17'
)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- GRANTS
-- =============================================================================

-- ml_service READS the switch on the inference path and must never write it: the switch is an
-- operator control, and a service that could set it could trip itself (the auto-trip ADR-0024
-- dec 6 defers). SELECT only — enforced here, not merely by convention in the code.
GRANT USAGE ON SCHEMA public TO ml_service_user;
GRANT SELECT ON public.platform_config TO ml_service_user;

-- No grant for alert_service_user: the alert worker does not run the feature engine — it reaches
-- inference over HTTP (`ML_SERVICE_URL/api/inference/predict`), so it is behind ml_service's switch
-- read and needs no access of its own.
