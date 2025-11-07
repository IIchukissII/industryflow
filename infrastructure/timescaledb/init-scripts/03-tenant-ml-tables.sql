-- Add ML Tables to Tenant Schema Template
-- Version: 5.0 - SCHEMA-PER-TENANT ARCHITECTURE
-- Date: November 7, 2025
-- Purpose: Extend tenant schema with ML tables

CREATE OR REPLACE FUNCTION add_ml_tables_to_schema(p_schema_name TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- =============================================================================
    -- ML MODELS
    -- =============================================================================
    
    EXECUTE format('
        CREATE TABLE %I.ml_models (
            model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipment_id UUID,
            model_name TEXT NOT NULL,
            description TEXT,
            model_type TEXT NOT NULL,
            model_version INTEGER DEFAULT 1,
            
            mlflow_run_id TEXT,
            mlflow_experiment_id TEXT,
            model_path TEXT,
            
            training_metrics JSONB,
            hyperparameters JSONB,
            feature_names TEXT[],
            sensor_ids UUID[],
            
            accuracy DOUBLE PRECISION,
            precision_score DOUBLE PRECISION,
            recall DOUBLE PRECISION,
            f1_score DOUBLE PRECISION,
            auc_roc DOUBLE PRECISION,
            
            training_samples INTEGER,
            training_start_date TIMESTAMPTZ,
            training_end_date TIMESTAMPTZ,
            
            status TEXT DEFAULT ''training'' 
                CHECK (status IN (''training'', ''active'', ''production'', ''deprecated'', ''failed'')),
            deployed_at TIMESTAMPTZ,
            deprecated_at TIMESTAMPTZ,
            
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            created_by TEXT,
            
            CONSTRAINT unique_model_version UNIQUE (model_name, model_version)
        )
    ', p_schema_name);
    
    EXECUTE format('CREATE INDEX idx_ml_models_status ON %I.ml_models(status)', p_schema_name);
    EXECUTE format('CREATE INDEX idx_ml_models_equipment ON %I.ml_models(equipment_id) WHERE equipment_id IS NOT NULL', p_schema_name);
    
    -- =============================================================================
    -- MODEL PREDICTIONS (HYPERTABLE WITH COMPRESSION)
    -- =============================================================================
    
    EXECUTE format('
        CREATE TABLE %I.model_predictions (
            prediction_id UUID NOT NULL DEFAULT gen_random_uuid(),
            timestamp TIMESTAMPTZ NOT NULL,
            
            model_id UUID NOT NULL,
            equipment_id UUID,
            sensor_id UUID NOT NULL,
            
            prediction INTEGER NOT NULL,
            confidence DOUBLE PRECISION,
            anomaly_score DOUBLE PRECISION,
            
            actual_label INTEGER,
            label_source TEXT,
            labeled_at TIMESTAMPTZ,
            labeled_by TEXT,
            
            model_version INTEGER,
            features_used JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            
            PRIMARY KEY (prediction_id, timestamp)
        )
    ', p_schema_name);
    
    PERFORM create_hypertable(format('%I.model_predictions', p_schema_name), 'timestamp', if_not_exists => TRUE);
    PERFORM set_chunk_time_interval(format('%I.model_predictions', p_schema_name), INTERVAL '7 days');
    
    -- Enable compression
    EXECUTE format('
        ALTER TABLE %I.model_predictions SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = ''model_id'',
            timescaledb.compress_orderby = ''timestamp DESC''
        )
    ', p_schema_name);
    
    PERFORM add_compression_policy(format('%I.model_predictions', p_schema_name), INTERVAL '30 days', if_not_exists => TRUE);
    PERFORM add_retention_policy(format('%I.model_predictions', p_schema_name), INTERVAL '180 days', if_not_exists => TRUE);
    
    EXECUTE format('CREATE INDEX idx_predictions_model_time ON %I.model_predictions(model_id, timestamp DESC)', p_schema_name);
    EXECUTE format('CREATE INDEX idx_predictions_labeled ON %I.model_predictions(timestamp DESC) WHERE actual_label IS NOT NULL', p_schema_name);
    
    -- =============================================================================
    -- LABELED PREDICTIONS VIEW
    -- =============================================================================
    
    EXECUTE format('
        CREATE OR REPLACE VIEW %I.labeled_predictions AS
        SELECT
            prediction_id,
            timestamp,
            model_id,
            equipment_id,
            sensor_id,
            prediction,
            actual_label,
            confidence,
            anomaly_score,
            (prediction = actual_label)::int AS correct_prediction
        FROM %I.model_predictions
        WHERE actual_label IS NOT NULL
    ', p_schema_name, p_schema_name);
    
    RAISE NOTICE '  ✓ ML tables added to schema: %', p_schema_name;
END;
$$;

COMMENT ON FUNCTION add_ml_tables_to_schema IS 'Adds ml_models and model_predictions tables to tenant schema';

DO $$
BEGIN
    RAISE NOTICE 'ML tables template function created';
END $$;
