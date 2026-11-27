# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The database, as ``tenant_usage_user``: two SECURITY DEFINER functions and no table (ADR-0049 dec 11)."""
from __future__ import annotations

import psycopg2

# The job's statements, read and run as written by infrastructure/timescaledb/tests/test_tenant_quotas.py.
MEASURE = "SELECT company_id::text, bytes FROM public.tenant_database_bytes()"
RECORD = "SELECT public.record_tenant_storage_usage(%s::uuid, %s, %s)"


class PostgresDatabase:
    def __init__(self, dsn_kwargs: dict):
        self._conn = psycopg2.connect(**dsn_kwargs)
        self._conn.autocommit = True

    def tenant_database_bytes(self) -> list[tuple[str, int]]:
        with self._conn.cursor() as cur:
            cur.execute(MEASURE)
            return [(row[0], int(row[1])) for row in cur.fetchall()]

    def record(self, company_id: str, store: str, size: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute(RECORD, (company_id, store, size))

    def close(self) -> None:
        self._conn.close()
