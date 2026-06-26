-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- ML Alert Integration Permissions Migration
-- Version: 1.0
-- Date: November 23, 2025
-- Purpose: Grant alert_service_user permissions to read feature configs and ML models
--          Required for equipment-level ML alert evaluation

-- =============================================================================
-- GRANT PERMISSIONS TO ALERT SERVICE USER
-- =============================================================================

CREATE OR REPLACE FUNCTION grant_ml_alert_permissions(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Grant SELECT on feature_engineering_configs table
    -- Alert service needs to read feature configs to know which sensors are required
    EXECUTE format('
        GRANT SELECT ON TABLE %I.feature_engineering_configs TO alert_service_user
    ', p_schema_name);

    -- Grant SELECT on ml_models table
    -- Alert service needs to read model metadata (feature_config_id, mlflow_run_id)
    EXECUTE format('
        GRANT SELECT ON TABLE %I.ml_models TO alert_service_user
    ', p_schema_name);

    RAISE NOTICE '  ✓ ML alert permissions granted in schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION grant_ml_alert_permissions IS
    'Grants alert_service_user SELECT permissions on feature_engineering_configs and ml_models for ML alert evaluation';

-- =============================================================================
-- APPLY TO ALL EXISTING TENANT SCHEMAS
-- =============================================================================

DO $$
DECLARE
    schema_name TEXT;
    granted_count INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'ML Alert Permissions Migration';
    RAISE NOTICE '========================================';

    -- Apply to all existing tenant schemas
    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
        ORDER BY nspname
    LOOP
        -- Check if tables exist before granting
        IF EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = schema_name
            AND table_name = 'feature_engineering_configs'
        ) THEN
            PERFORM grant_ml_alert_permissions(schema_name);
            granted_count := granted_count + 1;
        ELSE
            RAISE NOTICE '  ! Skipping % - feature_engineering_configs table not found', schema_name;
        END IF;
    END LOOP;

    RAISE NOTICE '========================================';
    RAISE NOTICE 'Migration Summary:';
    RAISE NOTICE '  - Schemas updated: %', granted_count;
    RAISE NOTICE '  - Tables: feature_engineering_configs, ml_models';
    RAISE NOTICE '  - Permissions: SELECT for alert_service_user';
    RAISE NOTICE '========================================';
END $$;

-- =============================================================================
-- VERIFICATION QUERY
-- =============================================================================

-- Run this to verify permissions were granted correctly:
/*
DO $$
DECLARE
    schema_name TEXT;
    has_feature_config_perm BOOLEAN;
    has_ml_models_perm BOOLEAN;
BEGIN
    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname LIKE 'tenant_%'
        ORDER BY nspname
        LIMIT 5  -- Check first 5 tenant schemas
    LOOP
        -- Check feature_engineering_configs permission
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.table_privileges
            WHERE table_schema = schema_name
            AND table_name = 'feature_engineering_configs'
            AND grantee = 'alert_service_user'
            AND privilege_type = 'SELECT'
        ) INTO has_feature_config_perm;

        -- Check ml_models permission
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.table_privileges
            WHERE table_schema = schema_name
            AND table_name = 'ml_models'
            AND grantee = 'alert_service_user'
            AND privilege_type = 'SELECT'
        ) INTO has_ml_models_perm;

        RAISE NOTICE 'Schema: % | feature_configs: % | ml_models: %',
            schema_name,
            has_feature_config_perm,
            has_ml_models_perm;
    END LOOP;
END $$;
*/
