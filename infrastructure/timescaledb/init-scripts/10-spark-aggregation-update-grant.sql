-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Spark Aggregation UPDATE-Grant Migration
-- Version: 1.0
-- Date: 2026-06-27
-- Purpose: Grant spark_streaming_user UPDATE on existing tenant tables. The aggregation job
--          upserts rollups with ON CONFLICT DO UPDATE (sensor_aggregations_1min/5min/1hour);
--          without UPDATE it fails "permission denied for table sensor_aggregations_1min".
--          New tenants get this from create_tenant_schema() (04-...); this backfills old ones.
--          Idempotent and safe to re-run.

DO $$
DECLARE
    schema_name TEXT;
    n INTEGER := 0;
BEGIN
    FOR schema_name IN
        SELECT nspname FROM pg_namespace WHERE nspname LIKE 'tenant_%' ORDER BY nspname
    LOOP
        EXECUTE format('GRANT UPDATE ON ALL TABLES IN SCHEMA %I TO spark_streaming_user', schema_name);
        n := n + 1;
    END LOOP;
    RAISE NOTICE 'Granted UPDATE to spark_streaming_user on % tenant schema(s)', n;
END $$;
