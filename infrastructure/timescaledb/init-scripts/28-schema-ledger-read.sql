-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: definition

-- Schema ledger read access (ADR-0034 dec 10)
-- Purpose: A database client that cannot rely on start order checks, as its own role, that the
--          ledger is current before its service starts (`migrate.sh verify --wait`). The ledger
--          (public.schema_ledger) is created and written by migrate.sh alone; this only lets the
--          platform's service roles read it. A role that is not created in this deployment (an
--          optional one whose password is unset) is skipped. Idempotent.

DO $$
DECLARE
    r TEXT;
BEGIN
    FOREACH r IN ARRAY ARRAY[
        'api_gateway_user', 'spark_streaming_user', 'alert_service_user', 'ml_service_user',
        'cold_export_user', 'tenant_delete_user', 'notebook_hub_reclaim_user', 'notebook_sql_proxy',
        'metrics_user'
    ] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('GRANT SELECT ON public.schema_ledger TO %I', r);
        END IF;
    END LOOP;
END $$;
