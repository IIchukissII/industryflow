-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Writers Read Tenant Status (ADR-0033 dec 3, ADR-0034 dec 1)
-- Purpose: The stream jobs and the alert worker write tenant data, so each refuses a tenant the
--          registry does not hold as active. They read exactly the two columns that decision needs:
--          which schema a registry entry names and whether it is active. Nothing else of the
--          registry (names, timestamps) reaches them.

GRANT SELECT (schema_name, is_active) ON public.companies TO spark_streaming_user;
GRANT SELECT (schema_name, is_active) ON public.companies TO alert_service_user;

DO $$
BEGIN
    RAISE NOTICE '✓ spark_streaming_user and alert_service_user read public.companies (schema_name, is_active)';
END
$$;
