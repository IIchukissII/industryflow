#!/bin/bash

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Database Roles and Permissions
# Version: 5.0 - Schema-per-tenant Architecture
# Date: November 8, 2025
# Purpose: Create application roles for schema-per-tenant architecture

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
        
        -- Ingestion Service Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ingestion_service_user') THEN
            EXECUTE format('CREATE ROLE ingestion_service_user WITH LOGIN PASSWORD %L', '${INGESTION_SERVICE_DB_PASSWORD}');
        END IF;
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
    ALTER ROLE ingestion_service_user CONNECTION LIMIT 300;
    ALTER ROLE spark_streaming_user CONNECTION LIMIT 50;
    ALTER ROLE alert_service_user CONNECTION LIMIT 10;
    ALTER ROLE ml_service_user CONNECTION LIMIT 10;
    ALTER ROLE mlflow_user CONNECTION LIMIT 20;
    
    -- Statement timeouts
    ALTER ROLE api_gateway_user SET statement_timeout = '30s';
    ALTER ROLE ingestion_service_user SET statement_timeout = '30s';
    ALTER ROLE spark_streaming_user SET statement_timeout = '120s';
    ALTER ROLE alert_service_user SET statement_timeout = '60s';
    ALTER ROLE ml_service_user SET statement_timeout = '60s';
    ALTER ROLE mlflow_user SET statement_timeout = '30s';
    
    -- =============================================================================
    -- PUBLIC SCHEMA PERMISSIONS
    -- =============================================================================
    
    -- API Gateway needs CREATE on public for user table
    GRANT CREATE ON SCHEMA public TO api_gateway_user;
    GRANT USAGE ON SCHEMA public TO api_gateway_user;
    
    -- Grant SELECT on public tables (companies, user) for API Gateway
    GRANT SELECT ON public.companies TO api_gateway_user;
    GRANT SELECT ON public."user" TO api_gateway_user;
    
    -- Other roles only need USAGE on public
    GRANT USAGE ON SCHEMA public TO spark_streaming_user;
    GRANT USAGE ON SCHEMA public TO alert_service_user;
    GRANT SELECT ON public."user" TO alert_service_user;
    GRANT SELECT ON public."user" TO ingestion_service_user;
    GRANT USAGE ON SCHEMA public TO ml_service_user;
    GRANT SELECT ON public."user" TO ml_service_user;
EOSQL

echo "✓ api_gateway_user (50 connections)"
echo "✓ ingestion_service_user (300 connections)"
echo "✓ spark_streaming_user (50 connections)"
echo "✓ alert_service_user (10 connections)"
echo "✓ ml_service_user (10 connections)"
echo "✓ mlflow_user (20 connections)"
echo "=========================================="
