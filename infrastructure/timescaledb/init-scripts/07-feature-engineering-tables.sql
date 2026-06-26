-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Feature Engineering Configuration Tables
-- Version: 1.0
-- Date: November 22, 2025
-- Purpose: Add feature engineering configuration support for flexible ML feature creation

CREATE OR REPLACE FUNCTION add_feature_engineering_tables_to_schema(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- =============================================================================
    -- FEATURE ENGINEERING CONFIGURATIONS
    -- =============================================================================

    EXECUTE format('
        CREATE TABLE IF NOT EXISTS %I.feature_engineering_configs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Identification
            equipment_type VARCHAR(100) NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,

            -- Configuration
            base_sensors JSONB NOT NULL,
            -- Example: ["xmeas_1", "xmeas_2", "xmeas_3", ...]
            -- List of sensor names required as inputs

            transformations JSONB NOT NULL,
            -- Example: [
            --   {"name": "xmeas_7_squared", "type": "polynomial", "sensor": "xmeas_7", "params": {"power": 2}},
            --   {"name": "xmeas_7_xmeas_8_ratio", "type": "interaction", "sensors": ["xmeas_7", "xmeas_8"], "params": {"operation": "ratio"}}
            -- ]

            -- Metadata
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            created_by TEXT,

            -- Ensure unique config per equipment type
            CONSTRAINT unique_equipment_config UNIQUE (equipment_type, name)
        )
    ', p_schema_name);

    -- Indexes
    EXECUTE format('CREATE INDEX IF NOT EXISTS idx_feature_configs_equipment
                    ON %I.feature_engineering_configs(equipment_type)', p_schema_name);

    -- Trigger for updated_at
    EXECUTE format('
        CREATE OR REPLACE FUNCTION %I.update_feature_config_timestamp()
        RETURNS TRIGGER AS $func$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $func$ LANGUAGE plpgsql
    ', p_schema_name);

    EXECUTE format('
        DROP TRIGGER IF EXISTS trigger_update_feature_config_timestamp
        ON %I.feature_engineering_configs
    ', p_schema_name);

    EXECUTE format('
        CREATE TRIGGER trigger_update_feature_config_timestamp
        BEFORE UPDATE ON %I.feature_engineering_configs
        FOR EACH ROW
        EXECUTE FUNCTION %I.update_feature_config_timestamp()
    ', p_schema_name, p_schema_name);

    RAISE NOTICE '  ✓ Feature engineering tables added to schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_feature_engineering_tables_to_schema IS
    'Adds feature_engineering_configs table to tenant schema for flexible ML feature configuration';

-- =============================================================================
-- EXTEND ML MODELS TABLE WITH FEATURE CONFIG REFERENCE
-- =============================================================================

CREATE OR REPLACE FUNCTION add_feature_config_columns_to_ml_models(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Add equipment_type and feature_config_id columns to ml_models if they don't exist
    EXECUTE format('
        ALTER TABLE %I.ml_models
        ADD COLUMN IF NOT EXISTS equipment_type VARCHAR(100),
        ADD COLUMN IF NOT EXISTS feature_config_id UUID
            REFERENCES %I.feature_engineering_configs(id) ON DELETE SET NULL
    ', p_schema_name, p_schema_name);

    EXECUTE format('
        CREATE INDEX IF NOT EXISTS idx_ml_models_equipment_type
        ON %I.ml_models(equipment_type) WHERE equipment_type IS NOT NULL
    ', p_schema_name);

    EXECUTE format('
        CREATE INDEX IF NOT EXISTS idx_ml_models_feature_config
        ON %I.ml_models(feature_config_id) WHERE feature_config_id IS NOT NULL
    ', p_schema_name);

    RAISE NOTICE '  ✓ Feature config columns added to ml_models in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_feature_config_columns_to_ml_models IS
    'Extends ml_models table with equipment_type and feature_config_id for feature engineering integration';

-- =============================================================================
-- APPLY TO EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
BEGIN
    -- Apply to all existing tenant schemas
    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
    LOOP
        PERFORM add_feature_engineering_tables_to_schema(schema_name);
        PERFORM add_feature_config_columns_to_ml_models(schema_name);
    END LOOP;

    RAISE NOTICE 'Feature engineering tables migration completed for all tenant schemas';
END $$;
