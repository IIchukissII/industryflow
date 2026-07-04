-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Model Drift Reference-Profile Migration (ADR-0021)
-- Version: 1.0
-- Date: July 4, 2026
-- Purpose: Extend ml_models with a reference_profile JSONB column — the per-model,
--          tenant-scoped training-window distribution snapshot that data/prediction
--          drift is evaluated against. A model with no reference_profile reports
--          "drift unavailable" until retrained with one (ADR-0021 decision 4).

-- =============================================================================
-- EXTEND ML MODELS TABLE WITH THE DRIFT REFERENCE PROFILE
-- =============================================================================

CREATE OR REPLACE FUNCTION add_reference_profile_columns_to_ml_models(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Compact distribution snapshot captured over the training window at
    -- registration time; nullable so pre-existing models remain valid and simply
    -- report drift unavailable until retrained.
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS reference_profile JSONB
    ', p_schema_name);

    RAISE NOTICE '  ✓ reference_profile column added to ml_models in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_reference_profile_columns_to_ml_models IS
    'Extends ml_models with the reference_profile JSONB drift baseline (ADR-0021)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Model Drift Reference-Profile Migration';
    RAISE NOTICE '========================================';

    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
        ORDER BY nspname
    LOOP
        -- Only touch schemas that actually have the ml_models table.
        IF EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = schema_name
            AND table_name = 'ml_models'
        ) THEN
            PERFORM add_reference_profile_columns_to_ml_models(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - ml_models table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', migrated_count;
    RAISE NOTICE '  - Column: ml_models.reference_profile JSONB';
    RAISE NOTICE '========================================';
END $$;
