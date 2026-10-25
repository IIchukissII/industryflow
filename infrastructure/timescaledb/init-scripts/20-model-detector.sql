-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Model Detector Migration (ADR-0010 dec 6, ADR-0028 dec 1-2)
-- Version: 1.0
-- Date: October 13, 2026
-- Purpose: Record, per model, the detector that scores it.
--
--          The detector is the declaration of what a model's output MEANS (ADR-0028 dec 1),
--          and inference dispatches on it (ADR-0010 dec 6). Without a column to hold it, the
--          choice made at the registration gate was discarded and every model was scored by
--          the compatibility default — including an uploaded model whose declared semantics
--          the gate had matched to a different detector.

-- =============================================================================
-- EXTEND ML MODELS TABLE WITH THE DETECTOR
-- =============================================================================

CREATE OR REPLACE FUNCTION add_detector_column_to_ml_models(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- No column default: registration always writes the detector it chose, and a row that
    -- reaches serving without one is refused there rather than scored by an assumed default.
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS detector TEXT
    ', p_schema_name);

    -- Backfill. Every model registered before ADR-0028 names the `sklearn` compatibility
    -- detector (ADR-0028, consequences), so a kernel-authored row, or one predating the
    -- provenance column, is recorded as exactly that.
    --
    -- An UPLOADED row is left NULL on purpose. Its declared semantics were judged and then
    -- thrown away, so nothing records which detector they matched; writing `sklearn` would be
    -- a guess about what its output means (ADR-0028 dec 2). NULL makes serving refuse it with
    -- a reason, and re-registering records the real answer.
    EXECUTE format('
        UPDATE %I.ml_models
        SET detector = ''sklearn''
        WHERE detector IS NULL
          AND (provenance IS NULL OR provenance = ''kernel'')
    ', p_schema_name);

    RAISE NOTICE '  ✓ detector added to ml_models in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_detector_column_to_ml_models IS
    'Extends ml_models with the detector that scores the model (ADR-0010 dec 6, ADR-0028 dec 1)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Model Detector Migration (ADR-0028)';
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
            -- The provenance column the backfill reads arrives with migration 19; a schema
            -- created by a create_tenant_schema() that predates this fix may lack it.
            PERFORM add_provenance_columns_to_ml_models(schema_name);
            PERFORM add_detector_column_to_ml_models(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - ml_models table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', migrated_count;
    RAISE NOTICE '  - Column: ml_models.detector';
    RAISE NOTICE '  - Kernel-authored and pre-provenance rows backfilled to ''sklearn''.';
    RAISE NOTICE '  - Uploaded rows left NULL: serving refuses them until re-registered.';
    RAISE NOTICE '========================================';
END $$;
