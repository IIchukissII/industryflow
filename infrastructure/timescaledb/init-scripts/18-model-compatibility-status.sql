-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Model Compatibility Status Migration (ADR-0027)
-- Version: 1.0
-- Date: July 14, 2026
-- Purpose: Extend ml_models with the compatibility verdict — does the environment this
--          model was TRAINED in still agree with the one that would SERVE it?
--
--          ADR-0027 decision 1: the artifact declares its requirements (MLflow writes them
--          into the run) and the serving environment satisfies them or refuses the model.
--          This column records the verdict of that comparison so an operator can SEE it on
--          the model, rather than discovering it when a prediction is quietly wrong.
--
--          Deliberately NOT an alert. ADR-0027 dec 8: `retrain_recommended` (ADR-0022) means
--          sustained drift AND label-derived precision decay — a statistical claim about the
--          world, which an operator labels true or false. A version mismatch is a mechanical
--          fact about two containers, with a different remedy (rebuild or re-register — the
--          weights may be perfectly fine). Routing it into that lane would put an unlabellable
--          alert in front of the person whose job is to label them.
--
--          NULLABLE, and null is meaningful: it means "never evaluated" — every model that
--          predates this migration. Those models are marked when they are next re-evaluated at
--          a gate; they are NOT torn out of the serving path they already occupy (dec 10),
--          because there is no training service to fix them with (ADR-0022 dec 4).

-- =============================================================================
-- EXTEND ML MODELS TABLE WITH THE COMPATIBILITY VERDICT
-- =============================================================================

CREATE OR REPLACE FUNCTION add_compatibility_columns_to_ml_models(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- The verdict itself. Three states (ADR-0027 dec 7):
    --   'compatible'   — the serving environment satisfies what the artifact declares.
    --   'patch_drift'  — the contract holds, but exact versions differ. scikit-learn warns on
    --                    load in this case; ADR-0027 shows that warning rather than swallowing it.
    --   'incompatible' — the contract does NOT hold. The model will not deploy.
    -- CHECK, not an enum type: a new state is then a migration, not a type rewrite.
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS compatibility_status TEXT
            CONSTRAINT ml_models_compatibility_status_check
            CHECK (compatibility_status IN (''compatible'', ''patch_drift'', ''incompatible''))
    ', p_schema_name);

    -- WHY the verdict is what it is: the declared-vs-installed versions and the reasons the
    -- rules fired. Without this a refusal is unactionable — "incompatible" tells a data
    -- scientist nothing about which library to move, or in which direction.
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS compatibility_detail JSONB
    ', p_schema_name);

    -- When the verdict was reached. It EXPIRES: the serving image can be rebuilt underneath a
    -- model that is sitting unpromoted, which is precisely why ADR-0027 dec 5 re-checks at
    -- deployment instead of trusting the verdict taken at registration.
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS compatibility_checked_at TIMESTAMPTZ
    ', p_schema_name);

    RAISE NOTICE '  ✓ compatibility_status/detail/checked_at added to ml_models in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_compatibility_columns_to_ml_models IS
    'Extends ml_models with the train/serve compatibility verdict (ADR-0027 dec 7)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Model Compatibility Status Migration';
    RAISE NOTICE '========================================';

    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
        ORDER BY nspname
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = schema_name
            AND table_name = 'ml_models'
        ) THEN
            PERFORM add_compatibility_columns_to_ml_models(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - ml_models table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', migrated_count;
    RAISE NOTICE '  - Columns: ml_models.compatibility_status / _detail / _checked_at';
    RAISE NOTICE '  - Existing models are left NULL (= never evaluated), deliberately:';
    RAISE NOTICE '    ADR-0027 dec 10 marks them going forward, it does not evict them.';
    RAISE NOTICE '========================================';
END $$;
