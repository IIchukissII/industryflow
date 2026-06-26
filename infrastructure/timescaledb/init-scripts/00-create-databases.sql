-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- IndustryFlow Database Initialization
-- Version: 3.0 - New Architecture
-- Date: November 7, 2025
-- Purpose: Create databases for IndustryFlow and MLflow

-- =============================================================================
-- CREATE DATABASES
-- =============================================================================

-- Main application database (if not exists)
SELECT 'CREATE DATABASE industryflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'industryflow')\gexec

-- MLflow backend database
SELECT 'CREATE DATABASE mlflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow')\gexec

-- Connect to industryflow database
\c industryflow

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'IndustryFlow Databases Created';
    RAISE NOTICE '========================================';
    RAISE NOTICE '  - industryflow: Main application database';
    RAISE NOTICE '  - mlflow: MLflow tracking database';
    RAISE NOTICE '  - TimescaleDB extension enabled';
    RAISE NOTICE '  - UUID extension enabled';
    RAISE NOTICE '========================================';
END $$;
