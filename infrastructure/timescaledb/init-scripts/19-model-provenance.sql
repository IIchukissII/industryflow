-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Model Provenance Migration (ADR-0030)
-- Version: 1.0
-- Date: July 16, 2026
-- Purpose: Extend ml_models with where a model CAME FROM, and — for one whose origin is
--          outside the platform — where its artifact actually is.
--
--          ADR-0030 dec 8: provenance is a first-class fact, never synthesized to look
--          internal. The platform does not fabricate a tracking run to make an uploaded
--          artifact fit the shape the registry already knows, however convenient that would
--          be: a synthetic run would make an environment the platform never observed look
--          observed, which is the same category of lie ADR-0028 exists to stop.
--
--          That refusal has a price, and this migration is where it is paid. Every model
--          until now was addressed THROUGH its run — the serving path asks the tracking
--          store which artifact a run produced. A model with no run cannot be addressed that
--          way, so an uploaded artifact would be registrable and never loadable unless its
--          location is recorded here. Declining to invent a run means recording the thing the
--          run would have told us.

-- =============================================================================
-- EXTEND ML MODELS TABLE WITH PROVENANCE
-- =============================================================================

CREATE OR REPLACE FUNCTION add_provenance_columns_to_ml_models(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Where this model was authored (ADR-0030 dec 8):
    --   'kernel'   — authored in the platform's own environment and logged through it. Its
    --                requirements were RECORDED by something that watched the training happen.
    --   'uploaded' — authored somewhere the platform never saw. Its requirements are ASSERTED,
    --                and it is held to a different gate (ADR-0030 dec 4-7).
    -- CHECK, not an enum type, mirroring migration 18: a new origin is then a migration rather
    -- than a type rewrite.
    --
    -- This is DERIVED at registration from how the model arrived, never accepted from the
    -- caller — it is the one fact most worth lying about, and the platform can see the answer
    -- for itself (a model the platform watched being made has the run it was made in).
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS provenance TEXT
            CONSTRAINT ml_models_provenance_check
            CHECK (provenance IN (''kernel'', ''uploaded''))
    ', p_schema_name);

    -- Where an uploaded model's artifact IS. NULL for a kernel-authored model, whose run
    -- answers this question — this column exists precisely because dec 8 refuses to give an
    -- uploaded model a run to answer it with.
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS artifact_uri TEXT
    ', p_schema_name);

    RAISE NOTICE '  ✓ provenance/artifact_uri added to ml_models in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_provenance_columns_to_ml_models IS
    'Extends ml_models with model provenance and the uploaded-artifact location (ADR-0030 dec 8)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Model Provenance Migration (ADR-0030)';
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
            PERFORM add_provenance_columns_to_ml_models(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - ml_models table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', migrated_count;
    RAISE NOTICE '  - Columns: ml_models.provenance / artifact_uri';
    RAISE NOTICE '  - Existing models are left NULL, deliberately. NULL does not mean';
    RAISE NOTICE '    "unknown origin": no model predating this migration can be an upload,';
    RAISE NOTICE '    because there was no way to upload one. It means the platform declines';
    RAISE NOTICE '    to assert MORE than it observed — backfilling ''kernel'' would claim';
    RAISE NOTICE '    these rows were authored in an environment nobody recorded (ADR-0028:';
    RAISE NOTICE '    the platform never guesses, and a likely guess is still a guess).';
    RAISE NOTICE '    The security-relevant question — "is this uploaded?" — NULL answers';
    RAISE NOTICE '    reliably: no.';
    RAISE NOTICE '========================================';
END $$;
