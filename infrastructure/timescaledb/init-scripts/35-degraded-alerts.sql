-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Degraded alerts as data (ADR-0044 dec 3, ADR-0022 dec 2-4).
--
-- An ML alert raised on a score computed with stateful features neutralized (the kill-switch off, or
-- an unavailable aggregate) records that it was degraded and which features, and the operator's label
-- on it copies whether it was, so precision can report such labels apart and the retrain trigger can
-- leave them out. Rows written before this change hold NULL: their degraded state is unknown, and
-- nothing is inferred from their message.

CREATE OR REPLACE FUNCTION add_degraded_columns_to_alerts(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- No default: the worker writes both for every alert it raises; NULL is a row from before.
    EXECUTE format('ALTER TABLE %I.alerts ADD COLUMN IF NOT EXISTS degraded BOOLEAN', p_schema_name);
    EXECUTE format('ALTER TABLE %I.alerts ADD COLUMN IF NOT EXISTS neutralized_features TEXT[]', p_schema_name);
    EXECUTE format('ALTER TABLE %I.alert_labels ADD COLUMN IF NOT EXISTS degraded BOOLEAN', p_schema_name);
    RAISE NOTICE '  ✓ degraded columns on alerts and alert_labels in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_degraded_columns_to_alerts IS
    'Records a degraded ML alert as data, and copies it onto its label (ADR-0044 dec 3, ADR-0022 dec 2)';

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    FOR schema_name IN
        SELECT nspname FROM pg_namespace WHERE nspname LIKE 'tenant_%' ORDER BY nspname
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema = schema_name AND table_name = 'alerts')
           AND EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = schema_name AND table_name = 'alert_labels') THEN
            PERFORM add_degraded_columns_to_alerts(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - alerts or alert_labels not found', schema_name;
        END IF;
    END LOOP;
    RAISE NOTICE 'Degraded alerts: % schema(s) updated', migrated_count;
END $$;
