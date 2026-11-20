#!/bin/bash

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# migration-kind: definition

# Database Roles and Permissions
# Version: 5.0 - Schema-per-tenant Architecture
# Date: November 8, 2025
# Purpose: Create application roles for schema-per-tenant architecture

set -e

echo "=========================================="
echo "Creating Application Roles"
echo "=========================================="

# Every role is created without a password; set_passwords below sets each one on every application.
# Quoted heredoc: no shell expansion; this part reads no secret.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    -- =============================================================================
    -- CREATE APPLICATION ROLES
    -- =============================================================================
    
    DO $$
    BEGIN
        -- API Gateway Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_gateway_user') THEN
            CREATE ROLE api_gateway_user WITH LOGIN;
        END IF;

        -- (No ingestion_service role: ingestion is mTLS-only and never touches the DB — ADR-0002.)

        -- Spark Streaming Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'spark_streaming_user') THEN
            CREATE ROLE spark_streaming_user WITH LOGIN;
        END IF;
        
        -- Alert Service Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'alert_service_user') THEN
            CREATE ROLE alert_service_user WITH LOGIN;
        END IF;
        
        -- ML Service Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ml_service_user') THEN
            CREATE ROLE ml_service_user WITH LOGIN;
        END IF;
        
        -- MLflow Role
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mlflow_user') THEN
            CREATE ROLE mlflow_user WITH LOGIN;
        END IF;

        -- Metrics exporter role: read-only monitoring via the built-in pg_monitor role
        -- (pg_stat_*, etc.) — NOT the superuser. Consumed by the postgres-exporter.
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metrics_user') THEN
            CREATE ROLE metrics_user WITH LOGIN;
        END IF;
        GRANT pg_monitor TO metrics_user;

        -- Cold-layer exporter role (ADR-0025). Reads raw sensor_measurements and drops the
        -- day's chunk after a verified Parquet export. Least privilege: it gets only SELECT on
        -- sensor_measurements plus EXECUTE on the SECURITY DEFINER cold_export_drop_day() (both
        -- granted per-tenant in 04 / migration 16) — never table ownership. Password may be unset
        -- when the cold-export job is not deployed; the role is still created so grants resolve.
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cold_export_user') THEN
            CREATE ROLE cold_export_user WITH LOGIN;
        END IF;

        -- Tenant-delete job role (ADR-0033 dec 4). It holds no table privilege: it reads the pending
        -- deletions and records each store's removal only through the SECURITY DEFINER functions of
        -- 26-company-deletion-job.sql, the last of which drops the tenant's database objects.
        -- Password may be unset when the job is not deployed; the role is still created so grants resolve.
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tenant_delete_user') THEN
            CREATE ROLE tenant_delete_user WITH LOGIN;
        END IF;

        -- Notebook hub reclaim role (ADR-0020 dec 6). No table privilege: it lists the users awaiting
        -- a volume reclaim and records that one store, only through 27-notebook-volume-reclaim.sql.
        -- Password may be unset when the notebook stack is not deployed; the role is still created.
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'notebook_hub_reclaim_user') THEN
            CREATE ROLE notebook_hub_reclaim_user WITH LOGIN;
        END IF;
    END $$;
    
    -- =============================================================================
    -- ROLE CONFIGURATION
    -- =============================================================================
    
    -- Connection limits. Must cover each service's (uvicorn workers x asyncpg pool) footprint:
    -- sized when a separate alert HTTP API (4 workers, pool min 5 / max 20) shared this role with the detector (retired by ADR-0031); 10 was too
    -- low to even start it — raised to 50 (in line with api_gateway_user). See migration 11.
    ALTER ROLE api_gateway_user CONNECTION LIMIT 50;
    ALTER ROLE spark_streaming_user CONNECTION LIMIT 50;
    ALTER ROLE alert_service_user CONNECTION LIMIT 50;
    ALTER ROLE ml_service_user CONNECTION LIMIT 10;
    ALTER ROLE mlflow_user CONNECTION LIMIT 20;
    ALTER ROLE metrics_user CONNECTION LIMIT 5;
    -- Single-node daily batch (one connection at a time); a small ceiling is plenty.
    ALTER ROLE cold_export_user CONNECTION LIMIT 5;
    -- One job, one connection at a time, plus its run lock.
    ALTER ROLE tenant_delete_user CONNECTION LIMIT 2;
    -- One hub service, one connection at a time.
    ALTER ROLE notebook_hub_reclaim_user CONNECTION LIMIT 2;

    -- Statement timeouts
    ALTER ROLE api_gateway_user SET statement_timeout = '30s';
    ALTER ROLE spark_streaming_user SET statement_timeout = '120s';
    ALTER ROLE alert_service_user SET statement_timeout = '60s';
    ALTER ROLE ml_service_user SET statement_timeout = '60s';
    ALTER ROLE mlflow_user SET statement_timeout = '30s';
    -- Batch scans over a day of raw across a hypertable can be slow; give the exporter room
    -- (a bounded ceiling, not disabled). It reads one day per statement.
    ALTER ROLE cold_export_user SET statement_timeout = '3600s';
    -- Dropping a tenant schema drops every chunk of its hypertables in one statement.
    ALTER ROLE tenant_delete_user SET statement_timeout = '1800s';
    ALTER ROLE notebook_hub_reclaim_user SET statement_timeout = '30s';
    
    -- =============================================================================
    -- PUBLIC SCHEMA PERMISSIONS
    -- =============================================================================
    
    -- No application role creates objects in public; the user table is defined by 29-user-table.sql.
    GRANT USAGE ON SCHEMA public TO api_gateway_user;

    -- Other roles need USAGE on the public schema
    GRANT USAGE ON SCHEMA public TO spark_streaming_user;
    GRANT USAGE ON SCHEMA public TO alert_service_user;
    GRANT USAGE ON SCHEMA public TO ml_service_user;
    -- Cold-export enumerates tenants from public.companies (verified identity — ADR-0025 dec 5).
    GRANT USAGE ON SCHEMA public TO cold_export_user;
    GRANT USAGE ON SCHEMA public TO tenant_delete_user;
    GRANT USAGE ON SCHEMA public TO notebook_hub_reclaim_user;

    -- SELECT on public.companies is granted in 01-init-schema.sql, after that table is
    -- created (this roles script runs before it).
EOSQL

# Each role's password, set on every application whether or not the role existed before, so
# re-applying this script sets a password enabled, rotated, or lost to a restore of the globals
# (dumped without passwords). A role whose variable is unset keeps the password it has. psql reads
# each password from the environment (\getenv) and quotes it itself (:'pw'): it is never in this
# script's text or on a command line; the server hashes it (password_encryption = scram-sha-256).
ROLE_PASSWORDS="
api_gateway_user API_GATEWAY_DB_PASSWORD
spark_streaming_user SPARK_STREAMING_DB_PASSWORD
alert_service_user ALERT_SERVICE_DB_PASSWORD
ml_service_user ML_SERVICE_DB_PASSWORD
mlflow_user MLFLOW_DB_PASSWORD
metrics_user METRICS_DB_PASSWORD
cold_export_user COLD_EXPORT_DB_PASSWORD
tenant_delete_user TENANT_DELETE_DB_PASSWORD
notebook_hub_reclaim_user NOTEBOOK_HUB_RECLAIM_DB_PASSWORD
"

set_passwords() {
  echo "SET client_min_messages = warning;"
  while read -r role var; do
    [ -n "$role" ] || continue
    [ -n "${!var:-}" ] || { echo "\\echo '  $role: $var unset, password unchanged'"; continue; }
    echo "\\getenv pw $var"
    echo "ALTER ROLE $role PASSWORD :'pw';"
    echo "\\echo '  $role: password set'"
  done <<< "$ROLE_PASSWORDS"
}

set_passwords | psql -X -q -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"

echo "✓ api_gateway_user (50 connections)"
echo "✓ spark_streaming_user (50 connections)"
echo "✓ alert_service_user (50 connections)"
echo "✓ ml_service_user (10 connections)"
echo "✓ mlflow_user (20 connections)"
echo "✓ metrics_user (5 connections, pg_monitor)"
echo "✓ cold_export_user (5 connections, SELECT + drop-chunk only)"
echo "✓ tenant_delete_user (2 connections, deletion functions only)"
echo "✓ notebook_hub_reclaim_user (2 connections, volume-reclaim functions only)"
echo "=========================================="
