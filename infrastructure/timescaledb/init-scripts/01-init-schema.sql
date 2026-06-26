-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- IndustryFlow Public Schema
-- Version: 5.0 - SCHEMA-PER-TENANT ARCHITECTURE
-- Date: November 7, 2025
-- Purpose: Public schema with only shared data (companies registry)

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- =============================================================================
-- PUBLIC SCHEMA: COMPANIES REGISTRY (Shared Across All Tenants)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.companies (
    company_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name VARCHAR(255) NOT NULL UNIQUE,
    schema_name VARCHAR(255) NOT NULL UNIQUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_companies_name ON public.companies(company_name);
CREATE INDEX IF NOT EXISTS idx_companies_schema ON public.companies(schema_name);
CREATE INDEX IF NOT EXISTS idx_companies_active ON public.companies(is_active) WHERE is_active = true;

COMMENT ON TABLE public.companies IS 'Company registry - each company gets its own schema';
COMMENT ON COLUMN public.companies.schema_name IS 'PostgreSQL schema name for tenant isolation';

-- Update trigger
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER companies_updated_at_trigger
    BEFORE UPDATE ON public.companies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- API Gateway reads the shared companies registry. Granted here, after the table
-- exists; the role was created earlier in 00-create-roles.sh.
GRANT SELECT ON public.companies TO api_gateway_user;

DO $$
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'SCHEMA-PER-TENANT Architecture';
    RAISE NOTICE '========================================';
    RAISE NOTICE '  ✓ Public schema: companies registry only';
    RAISE NOTICE '  ✓ Each tenant gets dedicated schema';
    RAISE NOTICE '  ✓ Complete data isolation';
    RAISE NOTICE '  ✓ Compression works (no RLS conflicts)';
    RAISE NOTICE '========================================';
END $$;
