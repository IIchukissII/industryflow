-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Alert Labels Migration (ADR-0022)
-- Version: 1.0
-- Date: July 5, 2026
-- Purpose: Add a per-tenant alert_labels table — the operator's correctness verdict on a
--          fired alert (true_positive / false_positive / unsure). This is the durable
--          concept-drift feedback signal, kept in its OWN table (not columns on the alerts
--          hypertable) so it survives the alerts' 90-day retention: the label is the training/
--          quality signal that must outlive the alert it describes (ADR-0022 decision 2).

-- =============================================================================
-- ALERT LABELS TABLE (per tenant, NOT a hypertable — no retention)
-- =============================================================================

CREATE OR REPLACE FUNCTION add_alert_labels_to_schema(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- One current verdict per alert (PRIMARY KEY alert_id, upserted on re-label). The
    -- rule/model/detection/severity are denormalized at label time so the verdict stays
    -- self-describing and groupable after the source alert row ages out of the 90-day
    -- alerts hypertable. Deliberately a plain table: no create_hypertable, no retention.
    EXECUTE format('
        CREATE TABLE IF NOT EXISTS %I.alert_labels (
            alert_id UUID PRIMARY KEY,
            triggered_at TIMESTAMPTZ,

            verdict TEXT NOT NULL CHECK (verdict IN (''true_positive'', ''false_positive'', ''unsure'')),
            note TEXT,

            rule_id UUID,
            model_id UUID,
            detection_type TEXT,
            severity TEXT,

            labeled_by TEXT NOT NULL,
            labeled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    ', p_schema_name);

    -- Precision-over-time (ADR-0022 dec 3) groups verdicts by model / rule over a window.
    EXECUTE format('CREATE INDEX IF NOT EXISTS idx_alert_labels_model ON %I.alert_labels(model_id, labeled_at DESC)', p_schema_name);
    EXECUTE format('CREATE INDEX IF NOT EXISTS idx_alert_labels_rule ON %I.alert_labels(rule_id, labeled_at DESC)', p_schema_name);
    EXECUTE format('CREATE INDEX IF NOT EXISTS idx_alert_labels_verdict ON %I.alert_labels(verdict)', p_schema_name);

    -- Grant explicitly. Unlike migration 14 (which added a COLUMN and inherited the table's
    -- grants), this is a NEW table: on the backfill path it would otherwise be ungranted, so
    -- api_gateway couldn't write labels and the notebook reader couldn't see them. Guarded on
    -- pg_roles so a partial environment doesn't error; idempotent on the new-tenant path where
    -- create_tenant_schema's GRANT block also covers it.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'api_gateway_user') THEN
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.alert_labels TO api_gateway_user', p_schema_name);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'alert_service_user') THEN
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I.alert_labels TO alert_service_user', p_schema_name);
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ml_service_user') THEN
        EXECUTE format('GRANT SELECT ON %I.alert_labels TO ml_service_user', p_schema_name);
    END IF;
    -- The per-tenant read-only role (notebook boundary, ADR-0011) — same SELECT it gets on
    -- every other tenant table. Role name derived as create_tenant_schema derives it.
    DECLARE
        v_reader_role TEXT := 'tenant_reader_' || substr(p_schema_name, 8);
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_reader_role) THEN
            EXECUTE format('GRANT SELECT ON %I.alert_labels TO %I', p_schema_name, v_reader_role);
        END IF;
    END;

    RAISE NOTICE '  ✓ alert_labels table added to schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_alert_labels_to_schema IS
    'Adds the alert_labels operator-feedback table to a tenant schema (ADR-0022)';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    migrated_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Alert Labels Migration (ADR-0022)';
    RAISE NOTICE '========================================';

    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
        ORDER BY nspname
    LOOP
        -- Only touch schemas that actually have the alerts table (i.e. real tenants).
        IF EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = schema_name
            AND table_name = 'alerts'
        ) THEN
            PERFORM add_alert_labels_to_schema(schema_name);
            migrated_count := migrated_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - alerts table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', migrated_count;
    RAISE NOTICE '  - Table: alert_labels (operator feedback, no retention)';
    RAISE NOTICE '========================================';
END $$;
