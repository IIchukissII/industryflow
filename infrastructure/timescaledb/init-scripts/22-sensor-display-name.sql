-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Sensor Display Name Migration (ADR-0034 dec 2-3)
-- Purpose: An optional human label per sensor.
--
--          sensor_name is the machine key: feature configs, model feature names and alert-rule
--          sensor patterns reference it, so it never changes. display_name is what a person
--          reads; NULL means "show the key".

-- =============================================================================
-- EXTEND SENSORS TABLE WITH THE DISPLAY NAME
-- =============================================================================

CREATE OR REPLACE FUNCTION add_display_name_column_to_sensors(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- No default and no backfill: an unset label falls back to the key at read time, so
    -- copying the key in would only freeze today's key into a column meant for people.
    EXECUTE format('
        ALTER TABLE %I.sensors
        ADD COLUMN IF NOT EXISTS display_name VARCHAR(100)
    ', p_schema_name);

    RAISE NOTICE '  ✓ display_name added to sensors in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_display_name_column_to_sensors IS
    'Extends sensors with an optional human label; sensor_name stays the machine key';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Sensor Display Name Migration';
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
            AND table_name = 'sensors'
        ) THEN
            PERFORM add_display_name_column_to_sensors(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - sensors table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', migrated_count;
    RAISE NOTICE '  - Column: sensors.display_name (nullable, no backfill)';
    RAISE NOTICE '========================================';
END $$;
