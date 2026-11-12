-- SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
--
-- SPDX-License-Identifier: AGPL-3.0-or-later

-- migration-kind: migration

-- A window's spread is zero for a window of one reading (ADR-0006 dec 9).
-- Purpose: the aggregation wrote no spread (NULL) for such a window; it now writes zero. Windows
--          already written are given the same value in every tenant's aggregate tables, so a
--          missing spread on an existing window is a defect, never served as unavailable
--          (ADR-0044 dec 3).

DO $$
DECLARE
    tbl RECORD;
    updated BIGINT;
    total BIGINT := 0;
BEGIN
    FOR tbl IN
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema LIKE 'tenant_%' AND table_name LIKE 'sensor\_aggregations\_%'
        ORDER BY table_schema, table_name
    LOOP
        EXECUTE format('UPDATE %I.%I SET stddev_value = 0 WHERE stddev_value IS NULL AND count_values = 1',
                       tbl.table_schema, tbl.table_name);
        GET DIAGNOSTICS updated = ROW_COUNT;
        total := total + updated;
    END LOOP;
    RAISE NOTICE 'Window spread of one reading: % window(s) set to zero', total;
END $$;
