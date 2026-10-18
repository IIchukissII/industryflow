-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Latest Reading Index Migration (ADR-0034 dec 3)
-- Purpose: Find one sensor's newest reading by index, whatever the volume of other sensors.
--
--          The natural key (time, sensor_id) leads with time: a "newest reading of this sensor"
--          lookup walks back through every other sensor's rows until it meets one of its own, and
--          a sensor silent for the whole window costs a walk over all of it.

-- =============================================================================
-- INDEX SENSOR MEASUREMENTS BY SENSOR, NEWEST FIRST
-- =============================================================================

CREATE OR REPLACE FUNCTION add_latest_reading_index_to_sensor_measurements(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS sensor_measurements_sensor_time_idx
        ON %I.sensor_measurements (sensor_id, time DESC)
    ', p_schema_name);

    RAISE NOTICE '  ✓ latest-reading index on sensor_measurements in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_latest_reading_index_to_sensor_measurements IS
    'Indexes sensor_measurements by (sensor_id, time DESC) for newest-reading-per-sensor lookups';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Latest Reading Index Migration';
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
            AND table_name = 'sensor_measurements'
        ) THEN
            PERFORM add_latest_reading_index_to_sensor_measurements(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - sensor_measurements table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', migrated_count;
    RAISE NOTICE '  - Index: sensor_measurements (sensor_id, time DESC)';
    RAISE NOTICE '========================================';
END $$;
