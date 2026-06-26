-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Complete Tenant Schema Creation
-- Version: 5.0 - SCHEMA-PER-TENANT ARCHITECTURE
-- Date: November 7, 2025
-- Purpose: Wrapper function that creates complete tenant with all tables

DROP FUNCTION IF EXISTS create_tenant_schema(UUID, VARCHAR);

CREATE OR REPLACE FUNCTION create_tenant_schema(
    p_company_id UUID,
    p_company_name VARCHAR(255)
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_schema_name TEXT;
BEGIN
    -- Generate schema name
    v_schema_name := 'tenant_' || REPLACE(p_company_id::TEXT, '-', '_');
    
    -- Check if schema already exists
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = v_schema_name) THEN
        RAISE NOTICE 'Schema % already exists, skipping creation', v_schema_name;
        RETURN v_schema_name;
    END IF;
    
    -- Create base schema
    EXECUTE format('CREATE SCHEMA %I', v_schema_name);
    
    -- Grant schema usage to application roles
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO api_gateway_user', v_schema_name);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO ingestion_service_user', v_schema_name);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO spark_streaming_user', v_schema_name);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO alert_service_user', v_schema_name);
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO ml_service_user', v_schema_name);
    
    RAISE NOTICE 'Creating tenant schema: % for company: %', v_schema_name, p_company_name;
    
    -- Call individual table creation functions
    PERFORM create_base_tables(v_schema_name);
    PERFORM add_alert_tables_to_schema(v_schema_name);
    PERFORM add_ml_tables_to_schema(v_schema_name);
    PERFORM add_feature_engineering_tables_to_schema(v_schema_name);
    PERFORM add_feature_config_columns_to_ml_models(v_schema_name);

    -- Grant table permissions
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO api_gateway_user', v_schema_name);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO ingestion_service_user', v_schema_name);
    EXECUTE format('GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA %I TO spark_streaming_user', v_schema_name);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO alert_service_user', v_schema_name);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO ml_service_user', v_schema_name);
    
    -- Grant sequence permissions
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO api_gateway_user', v_schema_name);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO spark_streaming_user', v_schema_name);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO alert_service_user', v_schema_name);
    EXECUTE format('GRANT USAGE ON ALL SEQUENCES IN SCHEMA %I TO ml_service_user', v_schema_name);
    
    -- Update companies table with schema name
    UPDATE public.companies 
    SET schema_name = v_schema_name 
    WHERE company_id = p_company_id;
    
    RAISE NOTICE '✓ Tenant schema created successfully: %', v_schema_name;
    RAISE NOTICE '  - Equipment, sensors, measurements (with compression)';
    RAISE NOTICE '  - 3 aggregation tables (with compression)';
    RAISE NOTICE '  - Alert rules and alerts';
    RAISE NOTICE '  - ML models and predictions';
    RAISE NOTICE '  - Feature engineering configurations';
    
    RETURN v_schema_name;
END;
$$;

COMMENT ON FUNCTION create_tenant_schema IS 'Creates complete isolated tenant schema - call from API on signup';

-- Helper function to create base tables (equipment, sensors, measurements, aggregations)
CREATE OR REPLACE FUNCTION create_base_tables(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Equipment table
    EXECUTE format('
        CREATE TABLE %I.equipment (
            equipment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipment_type VARCHAR(100) NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            site_id VARCHAR(100),
            location VARCHAR(255),
            sensor_count INTEGER NOT NULL CHECK (sensor_count > 0),
            expected_sensors UUID[] NOT NULL DEFAULT '{}',
            batch_timeout_seconds INTEGER NOT NULL DEFAULT 5,
            require_complete_batch BOOLEAN NOT NULL DEFAULT true,
            min_sensors_for_partial INTEGER,
            status VARCHAR(50) DEFAULT ''active'',
            commissioned_date DATE,
            last_maintenance_date DATE,
            next_maintenance_date DATE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by VARCHAR(255)
        )
    ', p_schema_name);
    
    -- Sensors table
    EXECUTE format('
        CREATE TABLE %I.sensors (
            sensor_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipment_id UUID NOT NULL REFERENCES %I.equipment(equipment_id) ON DELETE CASCADE,
            sensor_name VARCHAR(100) NOT NULL,
            sensor_type VARCHAR(50) NOT NULL,
            description TEXT,
            unit VARCHAR(20),
            min_value DOUBLE PRECISION,
            max_value DOUBLE PRECISION,
            normal_min DOUBLE PRECISION,
            normal_max DOUBLE PRECISION,
            position INTEGER NOT NULL,
            is_critical BOOLEAN DEFAULT false,
            is_required_for_ml BOOLEAN DEFAULT true,
            status VARCHAR(50) DEFAULT ''active'',
            is_active BOOLEAN DEFAULT true,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT unique_sensor_per_equipment UNIQUE (equipment_id, sensor_name)
        )
    ', p_schema_name, p_schema_name);
    
    -- Sensor measurements (hypertable)
    EXECUTE format('
        CREATE TABLE %I.sensor_measurements (
            time TIMESTAMPTZ NOT NULL,
            sensor_id UUID NOT NULL REFERENCES %I.sensors(sensor_id) ON DELETE CASCADE,
            equipment_id UUID NOT NULL REFERENCES %I.equipment(equipment_id) ON DELETE CASCADE,
            site_id TEXT NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            unit TEXT,
            quality_code INTEGER DEFAULT 1,
            is_anomaly BOOLEAN DEFAULT false,
            -- Natural key for idempotent upserts from the Spark streaming job (ADR-0006);
            -- includes the hypertable partition column (time).
            UNIQUE (time, sensor_id)
        )
    ', p_schema_name, p_schema_name, p_schema_name);
    
    PERFORM create_hypertable(format('%I.sensor_measurements', p_schema_name), 'time', if_not_exists => TRUE);
    PERFORM set_chunk_time_interval(format('%I.sensor_measurements', p_schema_name), INTERVAL '1 day');
    
    EXECUTE format('
        ALTER TABLE %I.sensor_measurements SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = ''equipment_id'',
            timescaledb.compress_orderby = ''time DESC''
        )
    ', p_schema_name);
    
    PERFORM add_compression_policy(format('%I.sensor_measurements', p_schema_name), INTERVAL '7 days', if_not_exists => TRUE);
    PERFORM add_retention_policy(format('%I.sensor_measurements', p_schema_name), INTERVAL '2 years', if_not_exists => TRUE);
    
    -- Create aggregation tables (1min, 5min, 1hour)
    PERFORM create_aggregation_tables(p_schema_name);
    
    RAISE NOTICE '  ✓ Base tables created in schema: %', p_schema_name;
END;
$$;

-- Helper to create aggregation tables
CREATE OR REPLACE FUNCTION create_aggregation_tables(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql  
AS $$
DECLARE
    agg_table TEXT;
    chunk_interval INTERVAL;
    compress_after INTERVAL;
    retention INTERVAL;
BEGIN
    FOREACH agg_table IN ARRAY ARRAY['sensor_aggregations_1min', 'sensor_aggregations_5min', 'sensor_aggregations_1hour']
    LOOP
        -- Set intervals based on aggregation level
        IF agg_table = 'sensor_aggregations_1min' THEN
            chunk_interval := INTERVAL '7 days';
            compress_after := INTERVAL '14 days';
            retention := INTERVAL '90 days';
        ELSIF agg_table = 'sensor_aggregations_5min' THEN
            chunk_interval := INTERVAL '30 days';
            compress_after := INTERVAL '30 days';
            retention := INTERVAL '180 days';
        ELSE
            chunk_interval := INTERVAL '90 days';
            compress_after := INTERVAL '60 days';
            retention := INTERVAL '1 year';
        END IF;
        
        EXECUTE format('
            CREATE TABLE %I.%I (
                time TIMESTAMPTZ NOT NULL,
                sensor_id UUID NOT NULL,
                equipment_id UUID NOT NULL,
                avg_value DOUBLE PRECISION,
                min_value DOUBLE PRECISION,
                max_value DOUBLE PRECISION,
                stddev_value DOUBLE PRECISION,
                count_values INTEGER,
                anomaly_count INTEGER DEFAULT 0,
                anomaly_percentage DOUBLE PRECISION,
                -- Natural key for idempotent upserts from the Spark aggregation job
                -- (ADR-0006). Includes the hypertable partition column (time), as
                -- TimescaleDB requires for a unique constraint.
                UNIQUE (time, sensor_id, equipment_id)
            )
        ', p_schema_name, agg_table);
        
        PERFORM create_hypertable(format('%I.%I', p_schema_name, agg_table), 'time', if_not_exists => TRUE);
        PERFORM set_chunk_time_interval(format('%I.%I', p_schema_name, agg_table), chunk_interval);
        
        EXECUTE format('
            ALTER TABLE %I.%I SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = ''equipment_id'',
                timescaledb.compress_orderby = ''time DESC''
            )
        ', p_schema_name, agg_table);
        
        PERFORM add_compression_policy(format('%I.%I', p_schema_name, agg_table), compress_after, if_not_exists => TRUE);
        PERFORM add_retention_policy(format('%I.%I', p_schema_name, agg_table), retention, if_not_exists => TRUE);
    END LOOP;
END;
$$;

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Complete Tenant Creation System Ready';
    RAISE NOTICE '========================================';
    RAISE NOTICE '  ✓ create_tenant_schema(uuid, name) - Main function';
    RAISE NOTICE '  ✓ Auto-creates: All tables + compression';
    RAISE NOTICE '  ✓ Auto-grants: Permissions to app roles';
    RAISE NOTICE '  ✓ Ready for API-driven tenant provisioning';
    RAISE NOTICE '========================================';
END $$;
