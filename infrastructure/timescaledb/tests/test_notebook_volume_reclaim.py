# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The notebook hub's window onto the deletion record (27-notebook-volume-reclaim.sql, ADR-0020 dec 6).

The hub lists the users of each deletion still awaiting a volume reclaim and records the
notebook_volumes store, and nothing else: no other store, no table, none of the job's functions.
Each call runs as notebook_hub_reclaim_user (SET ROLE), as the hub does.

Requires a running database with the init scripts applied (db-tenant-isolation.yml stands one
up); skips when none is reachable.
"""
import json
import uuid

import pytest

import test_company_deletion_job as deletion_job
from test_company_deletion_job import _as, _connect

# The deletion job's fixtures: a database, and a tenant with a deletion requested.
db = deletion_job.db
requested = deletion_job.requested

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors  # noqa: E402

HUB = "notebook_hub_reclaim_user"


def _pending(cur):
    return {str(c): [str(u) for u in users]
            for c, users in _as(cur, HUB, "SELECT * FROM public.pending_notebook_volume_reclaims()")}


def test_a_requested_deletion_awaits_its_reclaim_until_the_hub_records_it(requested):
    cur, company_id = requested
    assert company_id in _pending(cur)

    [(recorded,)] = _as(cur, HUB, "SELECT public.record_notebook_volumes_reclaimed(%s, %s::jsonb)",
                        (company_id, json.dumps({"volumes": 0})))

    assert recorded
    assert company_id not in _pending(cur)
    cur.execute("SELECT store, detail FROM public.company_deletion_steps WHERE company_id = %s", (company_id,))
    assert cur.fetchall() == [("notebook_volumes", {"volumes": 0})]
    # A repeat records nothing new.
    assert _as(cur, HUB, "SELECT public.record_notebook_volumes_reclaimed(%s, '{}'::jsonb)", (company_id,)) == [(False,)]


def test_the_users_listed_are_the_tenants_own(db):  # db: skips without a database
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.\"user\"') IS NULL")
        if not cur.fetchone()[0]:
            pytest.skip("the runtime user table exists here; its shape is the gateway's")
        # In one transaction, rolled back: a minimal user table, two tenants' users, one deletion.
        cur.execute('CREATE TABLE public."user" (id UUID PRIMARY KEY, company_id UUID NOT NULL)')
        company, other = str(uuid.uuid4()), str(uuid.uuid4())
        mine, theirs = sorted(str(uuid.uuid4()) for _ in range(2)), str(uuid.uuid4())
        for cid in (company, other):
            cur.execute("INSERT INTO public.companies (company_id, company_name, schema_name) VALUES (%s, %s, %s)",
                        (cid, f"reclaim-{cid}", f"reclaim_{cid.replace('-', '_')}"))
        cur.executemany('INSERT INTO public."user" (id, company_id) VALUES (%s, %s)',
                        [(u, company) for u in mine] + [(theirs, other)])
        cur.execute("INSERT INTO public.company_deletions (company_id, requested_by) VALUES (%s, %s)",
                    (company, str(uuid.uuid4())))
        pending = _pending(cur)
        assert pending[company] == mine and other not in pending
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("sql", [
    "SELECT public.record_company_deletion_step(%s, 'tracking_store', NULL, '{}'::jsonb)",
    "SELECT public.complete_company_deletion(%s, interval '1 day')",
    "SELECT * FROM public.company_deletion_user_ids(%s)",
    "SELECT * FROM public.company_deletions WHERE company_id = %s",
    "SELECT * FROM public.companies WHERE company_id = %s",
])
def test_the_hub_reaches_nothing_else(requested, sql):
    cur, company_id = requested
    with pytest.raises(errors.InsufficientPrivilege):
        _as(cur, HUB, sql, (company_id,))


def test_no_reclaim_is_recorded_without_a_pending_deletion(db):
    cur, _ = db
    with pytest.raises(errors.NoDataFound):
        _as(cur, HUB, "SELECT public.record_notebook_volumes_reclaimed(%s, '{}'::jsonb)", (str(uuid.uuid4()),))
