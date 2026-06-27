-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- Migration: read-only monitoring role for the postgres-exporter (existing deployments).
-- Granted the built-in pg_monitor role (pg_stat_* etc.) — never the superuser. The password
-- is set from METRICS_DB_PASSWORD at init; for an existing cluster set it once:
--   ALTER ROLE metrics_user PASSWORD '<value>';
-- Idempotent.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'metrics_user') THEN
        CREATE ROLE metrics_user WITH LOGIN;
    END IF;
    GRANT pg_monitor TO metrics_user;
END $$;

ALTER ROLE metrics_user CONNECTION LIMIT 5;
