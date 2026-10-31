-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: migration

-- Equipment carries no partial-batch settings (ADR-0044 dec 5).
-- Purpose: batch_timeout_seconds, require_complete_batch and min_sensors_for_partial were stored and
--          read by nothing; the platform scores only complete input. Dropped from every tenant's
--          equipment table; new tenants never get them (create_base_tables, script 04).

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    FOR schema_name IN
        SELECT nspname FROM pg_namespace WHERE nspname LIKE 'tenant_%' ORDER BY nspname
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = schema_name AND table_name = 'equipment'
        ) THEN
            EXECUTE format('
                ALTER TABLE %I.equipment
                    DROP COLUMN IF EXISTS batch_timeout_seconds,
                    DROP COLUMN IF EXISTS require_complete_batch,
                    DROP COLUMN IF EXISTS min_sensors_for_partial
            ', schema_name);
            migrated_count := migrated_count + 1;
        END IF;
    END LOOP;
    RAISE NOTICE 'Equipment partial-batch settings dropped in % tenant schema(s)', migrated_count;
END $$;
