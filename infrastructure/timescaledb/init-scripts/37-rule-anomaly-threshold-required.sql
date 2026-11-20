-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- An alert rule always holds its anomaly threshold.
--
-- The column was nullable, and an ML rule stored with NULL was scored with no threshold: the model
-- refused every request and the rule never fired. A rule stored so is given the column's default,
-- the value a rule created without one already holds, and the column refuses NULL from now on.

CREATE OR REPLACE FUNCTION add_anomaly_threshold_required_to_rules(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    EXECUTE format('UPDATE %I.alert_rules SET anomaly_threshold = DEFAULT WHERE anomaly_threshold IS NULL',
                   p_schema_name);
    EXECUTE format('ALTER TABLE %I.alert_rules ALTER COLUMN anomaly_threshold SET NOT NULL', p_schema_name);
    RAISE NOTICE '  ✓ alert_rules.anomaly_threshold required in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_anomaly_threshold_required_to_rules IS
    'An alert rule always holds its anomaly threshold: NULL is refused';

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    FOR schema_name IN
        SELECT nspname FROM pg_namespace WHERE nspname LIKE 'tenant_%' ORDER BY nspname
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = schema_name AND table_name = 'alert_rules') THEN
            PERFORM add_anomaly_threshold_required_to_rules(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - alert_rules not found', schema_name;
        END IF;
    END LOOP;
    RAISE NOTICE 'Rule anomaly threshold: % schema(s) updated', migrated_count;
END $$;
