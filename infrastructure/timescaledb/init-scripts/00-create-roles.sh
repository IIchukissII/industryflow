#!/bin/bash
# Database Roles and Permissions
# Version: 4.0 - NEW ARCHITECTURE
# Date: November 7, 2025
# Purpose: Create application roles with RLS support

set -e

echo "=========================================="
echo "Creating Application Roles"
echo "=========================================="

# Create roles with passwords from environment variables
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    -- =============================================================================
    -- CREATE APPLICATION ROLES
    -- =============================================================================
    
    DO \$\$
    BEGIN
        -- API Gateway Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_gateway_user') THEN
            EXECUTE format('CREATE ROLE api_gateway_user WITH LOGIN PASSWORD %L', '${API_GATEWAY_DB_PASSWORD}');
        END IF;
        
        -- Spark Streaming Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'spark_streaming_user') THEN
            EXECUTE format('CREATE ROLE spark_streaming_user WITH LOGIN PASSWORD %L', '${SPARK_STREAMING_DB_PASSWORD}');
        END IF;
        
        -- Alert Service Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'alert_service_user') THEN
            EXECUTE format('CREATE ROLE alert_service_user WITH LOGIN PASSWORD %L', '${ALERT_SERVICE_DB_PASSWORD}');
        END IF;
        
        -- ML Service Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ml_service_user') THEN
            EXECUTE format('CREATE ROLE ml_service_user WITH LOGIN PASSWORD %L', '${ML_SERVICE_DB_PASSWORD}');
        END IF;
        
        -- MLflow Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mlflow_user') THEN
            EXECUTE format('CREATE ROLE mlflow_user WITH LOGIN PASSWORD %L', '${MLFLOW_DB_PASSWORD}');
        END IF;
    END \$\$;
    
    -- =============================================================================
    -- ROLE CONFIGURATION
    -- =============================================================================
    
    -- Connection limits
    ALTER ROLE api_gateway_user CONNECTION LIMIT 50;
    ALTER ROLE spark_streaming_user CONNECTION LIMIT 100;
    ALTER ROLE alert_service_user CONNECTION LIMIT 30;
    ALTER ROLE ml_service_user CONNECTION LIMIT 30;
    ALTER ROLE mlflow_user CONNECTION LIMIT 20;
    
    -- Statement timeouts
    ALTER ROLE api_gateway_user SET statement_timeout = '30s';
    ALTER ROLE spark_streaming_user SET statement_timeout = '120s';
    ALTER ROLE alert_service_user SET statement_timeout = '60s';
    ALTER ROLE ml_service_user SET statement_timeout = '60s';
    ALTER ROLE mlflow_user SET statement_timeout = '30s';
    
    -- Allow RLS context setting
    ALTER ROLE api_gateway_user SET app.current_company_id = '';
    ALTER ROLE spark_streaming_user SET app.current_company_id = '';
    ALTER ROLE alert_service_user SET app.current_company_id = '';
    ALTER ROLE ml_service_user SET app.current_company_id = '';
EOSQL

echo "✓ api_gateway_user"
echo "✓ spark_streaming_user"
echo "✓ alert_service_user"
echo "✓ ml_service_user"
echo "✓ mlflow_user"
echo "=========================================="
