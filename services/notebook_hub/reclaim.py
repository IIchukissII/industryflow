# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The hub's reclaim of a deleted tenant's notebook volumes (ADR-0020 dec 6, ADR-0033 dec 4).

A hub-managed service. It reads the deletions still awaiting a reclaim from the platform's deletion
record, deletes each of the tenant's users from the hub, and records the notebook_volumes store once
every one is gone. Deleting a hub user stops its servers and has its spawner remove the user's volume
(``delete_forever``), so the volume goes through the machinery that created it. A user the hub never
saw never authored, so has no volume; it counts as reclaimed.

Its hub token can delete users and nothing else; its database identity can list pending reclaims and
record this one store and nothing else (27-notebook-volume-reclaim.sql). Both stay inside the hub.

Env: JUPYTERHUB_API_URL, JUPYTERHUB_API_TOKEN (set by the hub), NOTEBOOK_RECLAIM_DB_* (the database
and notebook_hub_reclaim_user), NOTEBOOK_RECLAIM_EVERY_SECONDS.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable

logger = logging.getLogger("notebook_reclaim")

# The outcome of deleting one hub user.
DELETED, ABSENT, BUSY = "deleted", "absent", "busy"


def reclaim_once(pending: Iterable, delete_user: Callable[[str], str], record: Callable[[str, dict], None]) -> dict:
    """One pass: company_id -> what was done. A tenant with a user still stopping is left for the next pass."""
    done = {}
    for company_id, user_ids in pending:
        outcomes = {}
        for user_id in user_ids:
            try:
                outcomes[str(user_id)] = delete_user(str(user_id))
            except Exception:  # noqa: BLE001 - one user's failure leaves the tenant for the next pass
                logger.exception("tenant %s: user %s not deleted from the hub", company_id, user_id)
                outcomes[str(user_id)] = BUSY
        if BUSY in outcomes.values():
            logger.info("tenant %s: not every user is deleted yet; the next pass resumes", company_id)
            continue
        detail = {"hub_users_deleted": sum(o == DELETED for o in outcomes.values()),
                  "users_never_in_hub": sum(o == ABSENT for o in outcomes.values())}
        record(str(company_id), detail)
        logger.info("tenant %s: notebook volumes reclaimed %s", company_id, detail)
        done[str(company_id)] = detail
    return done


# --------------------------------------------------------------------- live adapters (hub-bound)

def _delete_from_hub(api_url: str, token: str):
    def delete_user(user_id: str) -> str:
        request = urllib.request.Request(f"{api_url.rstrip('/')}/users/{urllib.parse.quote(user_id)}",
                                         method="DELETE", headers={"Authorization": f"token {token}"})
        try:
            with urllib.request.urlopen(request, timeout=300):
                return DELETED
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ABSENT
            if e.code == 400:  # its server is still stopping
                return BUSY
            raise
    return delete_user


def _db():  # pragma: no cover - needs the database
    import psycopg2
    import psycopg2.extras

    # uuid and uuid[] as UUIDs: without it psycopg2 hands an array back as its text, '{...}'.
    psycopg2.extras.register_uuid()
    return psycopg2.connect(
        host=os.environ["NOTEBOOK_RECLAIM_DB_HOST"],
        port=os.environ.get("NOTEBOOK_RECLAIM_DB_PORT", "5432"),
        dbname=os.environ.get("NOTEBOOK_RECLAIM_DB_NAME", "industryflow"),
        user=os.environ.get("NOTEBOOK_RECLAIM_DB_USER", "notebook_hub_reclaim_user"),
        password=os.environ["NOTEBOOK_RECLAIM_DB_PASSWORD"],
        sslmode=os.environ.get("NOTEBOOK_RECLAIM_DB_SSLMODE", "verify-full"),
        sslrootcert=os.environ.get("NOTEBOOK_RECLAIM_DB_SSLROOTCERT", "/etc/ssl/industryflow/ca.crt"),
        connect_timeout=10,
    )


def main() -> int:  # pragma: no cover - the hub runs it
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    every = int(os.environ.get("NOTEBOOK_RECLAIM_EVERY_SECONDS", "300"))
    delete_user = _delete_from_hub(os.environ["JUPYTERHUB_API_URL"], os.environ["JUPYTERHUB_API_TOKEN"])
    while True:
        conn = None
        try:
            conn = _db()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM public.pending_notebook_volume_reclaims()")
                pending = cur.fetchall()

                def record(company_id, detail):
                    cur.execute("SELECT public.record_notebook_volumes_reclaimed(%s, %s::jsonb)",
                                (company_id, json.dumps(detail)))

                reclaim_once(pending, delete_user, record)
        except Exception:  # noqa: BLE001 - a failed pass is retried on the next
            logger.exception("reclaim pass failed")
        finally:
            if conn is not None:
                conn.close()
        time.sleep(every)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
