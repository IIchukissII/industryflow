-- Seed Test Tenants (Development Only)
-- Version: 5.0 - SCHEMA-PER-TENANT ARCHITECTURE
-- Date: November 7, 2025
-- Purpose: Create test companies with isolated schemas

-- =============================================================================
-- CREATE TEST COMPANIES
-- =============================================================================

DO $$
DECLARE
    v_acme_id UUID := '550e8400-e29b-41d4-a716-446655440000';
    v_techcorp_id UUID := '550e8400-e29b-41d4-a716-446655440001';
    v_global_id UUID := '550e8400-e29b-41d4-a716-446655440002';
    v_schema_name TEXT;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Creating Test Tenants';
    RAISE NOTICE '========================================';
    
    -- ACME Manufacturing
    INSERT INTO public.companies (company_id, company_name, schema_name)
    VALUES (v_acme_id, 'ACME Manufacturing', '')
    ON CONFLICT (company_name) DO NOTHING;
    
    v_schema_name := create_tenant_schema(v_acme_id, 'ACME Manufacturing');
    RAISE NOTICE '✓ ACME Manufacturing: %', v_schema_name;
    
    -- TechCorp Industries
    INSERT INTO public.companies (company_id, company_name, schema_name)
    VALUES (v_techcorp_id, 'TechCorp Industries', '')
    ON CONFLICT (company_name) DO NOTHING;
    
    v_schema_name := create_tenant_schema(v_techcorp_id, 'TechCorp Industries');
    RAISE NOTICE '✓ TechCorp Industries: %', v_schema_name;
    
    -- Global Systems Inc
    INSERT INTO public.companies (company_id, company_name, schema_name)
    VALUES (v_global_id, 'Global Systems Inc', '')
    ON CONFLICT (company_name) DO NOTHING;
    
    v_schema_name := create_tenant_schema(v_global_id, 'Global Systems Inc');
    RAISE NOTICE '✓ Global Systems Inc: %', v_schema_name;
    
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Test Tenants Created Successfully';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Each tenant has complete isolation:';
    RAISE NOTICE '  - Dedicated PostgreSQL schema';
    RAISE NOTICE '  - All tables with compression';
    RAISE NOTICE '  - Ready for data ingestion';
    RAISE NOTICE '========================================';
END $$;
