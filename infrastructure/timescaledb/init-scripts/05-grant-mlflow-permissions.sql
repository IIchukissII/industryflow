-- Grant MLflow Database Permissions
-- Version: 5.0 - SCHEMA-PER-TENANT ARCHITECTURE
-- Date: November 7, 2025
-- Purpose: Grant mlflow_user full access to mlflow database

\c mlflow

-- Grant schema permissions
GRANT ALL PRIVILEGES ON SCHEMA public TO mlflow_user;

-- Grant database permissions
GRANT ALL PRIVILEGES ON DATABASE mlflow TO mlflow_user;

-- Grant table permissions (for existing and future tables)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO mlflow_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO mlflow_user;

-- Grant default privileges for future objects
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO mlflow_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO mlflow_user;

-- Allow mlflow_user to create tables
GRANT CREATE ON SCHEMA public TO mlflow_user;

\c industryflow

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'MLflow Permissions Granted';
    RAISE NOTICE '========================================';
    RAISE NOTICE '  ✓ mlflow_user can create tables in mlflow database';
    RAISE NOTICE '  ✓ Full access to public schema';
    RAISE NOTICE '========================================';
END $$;
