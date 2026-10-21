# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The job's database side: the SECURITY DEFINER functions of 26-company-deletion-job.sql.

tenant_delete_user holds no table privilege; everything here is one of those function calls. Each
call is its own short connection (the role's connection limit is small), except the run lock, which
holds one session for the whole run.
"""
from __future__ import annotations

import json
from contextlib import closing, contextmanager

import psycopg2

from .job import Removal

# A session-level advisory lock for the whole run: two runs never work the same deletion.
RUN_LOCK_KEY = (0x49464C57, 0x44454C54)  # "IFLW", "DELT"


class Registry:
    def __init__(self, dsn_kwargs: dict):
        self._dsn = dsn_kwargs

    def _connect(self):
        conn = psycopg2.connect(**self._dsn)
        conn.autocommit = True
        return closing(conn)

    def _call(self, sql: str, args=()):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall() if cur.description else None

    @contextmanager
    def run_lock(self):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", RUN_LOCK_KEY)
            held = cur.fetchone()[0]
            try:
                yield held
            finally:
                if held:
                    cur.execute("SELECT pg_advisory_unlock(%s, %s)", RUN_LOCK_KEY)

    def required_stores(self) -> list:
        [(stores,)] = self._call("SELECT public.company_deletion_required_stores()")
        return list(stores)

    def pending(self) -> list:
        return [(str(c), at, list(removed))
                for c, at, removed in self._call("SELECT * FROM public.pending_company_deletions()")]

    def user_ids(self, company_id: str) -> list:
        return [str(u) for (u,) in self._call("SELECT * FROM public.company_deletion_user_ids(%s)",
                                              (company_id,))]

    def record(self, company_id: str, store: str, removal: Removal) -> None:
        self._call("SELECT public.record_company_deletion_step(%s, %s, %s, %s::jsonb)",
                   (company_id, store, removal.retained_until, json.dumps(removal.detail)))

    def complete(self, company_id: str, backup_retention) -> dict:
        [(detail,)] = self._call("SELECT public.complete_company_deletion(%s, %s)",
                                 (company_id, backup_retention))
        return detail
