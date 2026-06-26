-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Add Alert Tables to Tenant Schema Template
-- Version: 5.0 - SCHEMA-PER-TENANT ARCHITECTURE
-- Date: November 7, 2025
-- Purpose: Extend create_tenant_schema to include alert tables

CREATE OR REPLACE FUNCTION add_alert_tables_to_schema(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- =============================================================================
    -- ALERT RULES
    -- =============================================================================
    
    EXECUTE format('
        CREATE TABLE %I.alert_rules (
            rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            
            sensor_id UUID,
            equipment_id UUID,
            sensor_pattern TEXT,
            site_id TEXT,
            
            detection_type TEXT NOT NULL DEFAULT ''threshold'' 
                CHECK (detection_type IN (''threshold'', ''ml'', ''statistical'')),
            
            condition TEXT,
            threshold DOUBLE PRECISION,
            threshold_min DOUBLE PRECISION,
            threshold_max DOUBLE PRECISION,
            
            model_id UUID,
            anomaly_threshold DOUBLE PRECISION DEFAULT 0.85,
            model_config JSONB,
            
            requires_complete_batch BOOLEAN DEFAULT false,
            min_batch_completeness DOUBLE PRECISION DEFAULT 1.0,
            
            severity TEXT NOT NULL DEFAULT ''medium''
                CHECK (severity IN (''low'', ''medium'', ''high'', ''critical'')),
            priority INTEGER DEFAULT 0,
            enabled BOOLEAN DEFAULT true,
            
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            created_by TEXT
        )
    ', p_schema_name);
    
    EXECUTE format('CREATE INDEX idx_alert_rules_enabled ON %I.alert_rules(enabled) WHERE enabled = true', p_schema_name);
    
    -- =============================================================================
    -- ALERTS (HYPERTABLE)
    -- =============================================================================
    
    EXECUTE format('
        CREATE TABLE %I.alerts (
            alert_id UUID NOT NULL DEFAULT gen_random_uuid(),
            triggered_at TIMESTAMPTZ NOT NULL,
            rule_id UUID,
            
            sensor_id UUID,
            equipment_id UUID,
            site_id TEXT,
            
            detection_type TEXT NOT NULL,
            threshold_value DOUBLE PRECISION,
            actual_value DOUBLE PRECISION,
            condition TEXT,
            
            model_id UUID,
            anomaly_score DOUBLE PRECISION,
            affected_sensors UUID[],
            
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            
            acknowledged BOOLEAN DEFAULT false,
            acknowledged_at TIMESTAMPTZ,
            acknowledged_by TEXT,
            
            created_at TIMESTAMPTZ DEFAULT NOW(),
            
            PRIMARY KEY (alert_id, triggered_at)
        )
    ', p_schema_name);
    
    PERFORM create_hypertable(format('%I.alerts', p_schema_name), 'triggered_at', if_not_exists => TRUE);
    PERFORM set_chunk_time_interval(format('%I.alerts', p_schema_name), INTERVAL '7 days');
    PERFORM add_retention_policy(format('%I.alerts', p_schema_name), INTERVAL '90 days', if_not_exists => TRUE);
    
    EXECUTE format('CREATE INDEX idx_alerts_unacknowledged ON %I.alerts(triggered_at DESC) WHERE acknowledged = false', p_schema_name);
    
    RAISE NOTICE '  ✓ Alert tables added to schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_alert_tables_to_schema IS 'Adds alert_rules and alerts tables to tenant schema';

DO $$
BEGIN
    RAISE NOTICE 'Alert tables template function created';
END $$;
