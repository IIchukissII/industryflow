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
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- =============================================================================
    -- CREATE APPLICATION ROLES
    -- =============================================================================
    
    DO \$\$
    BEGIN
        -- API Gateway Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_gateway_user') THEN
            EXECUTE format('CREATE ROLE api_gateway_user WITH LOGIN PASSWORD %L', '${API_GATEWAY_DB_PASSWORD}');
        END IF;

        -- (No ingestion_service role: ingestion is mTLS-only and never touches the DB — ADR-0002.)

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

        -- Metrics exporter role: read-only monitoring via the built-in pg_monitor role
        -- (pg_stat_*, etc.) — NOT the superuser. Consumed by the postgres-exporter.
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metrics_user') THEN
            EXECUTE format('CREATE ROLE metrics_user WITH LOGIN PASSWORD %L', '${METRICS_DB_PASSWORD}');
        END IF;
        GRANT pg_monitor TO metrics_user;
    END \$\$;
    
    -- =============================================================================
    -- ROLE CONFIGURATION
    -- =============================================================================
    
    -- Connection limits. Must cover each service's (uvicorn workers x asyncpg pool) footprint:
    -- alert-service-api runs 4 workers (pool min 5 / max 20) plus the detector, so 10 was too
    -- low to even start it — raised to 50 (in line with api_gateway_user). See migration 11.
    ALTER ROLE api_gateway_user CONNECTION LIMIT 50;
    ALTER ROLE spark_streaming_user CONNECTION LIMIT 50;
    ALTER ROLE alert_service_user CONNECTION LIMIT 50;
    ALTER ROLE ml_service_user CONNECTION LIMIT 10;
    ALTER ROLE mlflow_user CONNECTION LIMIT 20;
    ALTER ROLE metrics_user CONNECTION LIMIT 5;
    
    -- Statement timeouts
    ALTER ROLE api_gateway_user SET statement_timeout = '30s';
    ALTER ROLE spark_streaming_user SET statement_timeout = '120s';
    ALTER ROLE alert_service_user SET statement_timeout = '60s';
    ALTER ROLE ml_service_user SET statement_timeout = '60s';
    ALTER ROLE mlflow_user SET statement_timeout = '30s';
    
    -- =============================================================================
    -- PUBLIC SCHEMA PERMISSIONS
    -- =============================================================================
    
    -- API Gateway needs CREATE on public to create the (runtime) user table
    GRANT CREATE ON SCHEMA public TO api_gateway_user;
    GRANT USAGE ON SCHEMA public TO api_gateway_user;

    -- Other roles need USAGE on the public schema
    GRANT USAGE ON SCHEMA public TO spark_streaming_user;
    GRANT USAGE ON SCHEMA public TO alert_service_user;
    GRANT USAGE ON SCHEMA public TO ml_service_user;

    -- public."user" is created at runtime by api_gateway_user (fastapi-users), so it
    -- does NOT exist yet at init time. Granting SELECT on it directly here would abort
    -- init (relation does not exist). Instead grant it via default privileges so the
    -- reader roles automatically receive SELECT when api_gateway_user creates the table.
    ALTER DEFAULT PRIVILEGES FOR ROLE api_gateway_user IN SCHEMA public
        GRANT SELECT ON TABLES TO alert_service_user, ml_service_user;

    -- SELECT on public.companies is granted in 01-init-schema.sql, after that table is
    -- created (this roles script runs before it).
EOSQL

echo "✓ api_gateway_user (50 connections)"
echo "✓ spark_streaming_user (50 connections)"
echo "✓ alert_service_user (10 connections)"
echo "✓ ml_service_user (10 connections)"
echo "✓ mlflow_user (20 connections)"
echo "=========================================="
