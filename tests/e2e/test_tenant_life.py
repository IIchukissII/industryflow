# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's life through deletion (ADR-0026 dec 10; ADR-0033): provisioned, used, suspended (its
users and sessions refused at once), its deletion refused until the suspension is old enough, then
removed from every store by the deletion job and the operator steps it waits on, leaving a tombstone.

The suspension's age is the one thing set from inside: the deletion floor is thirty days of wall
clock (ADR-0033 dec 7). The job also carries out what earlier runs left requested."""
import time
import uuid


from harness import pretty

DELETION_TIMEOUT = 1200


def test_a_tenant_is_provisioned_used_suspended_and_deleted_from_every_store(edge, root, env):
    run = uuid.uuid4().hex[:8]
    cid = root.post("/api/companies", json={"company_name": f"e2e-life-{run}"}).json()["company_id"]
    email, password = f"e2e-life-{run}@example.com", f"E2e-{uuid.uuid4().hex}"
    root.post("/api/users", json={"email": email, "password": password, "role": "admin", "company_id": cid})
    admin = edge.session(email, password)
    admin.get("/api/equipment")

    # Suspended: its users and their live sessions are refused at once (ADR-0033 dec 3).
    root.put(f"/api/companies/{cid}", json={"is_active": False})
    admin.get("/api/equipment", expect=(401, 403))
    assert edge.login(email, password).status_code in (400, 401, 403)

    # Deletion waits for the suspension's floor (ADR-0033 dec 7), then nothing reactivates it.
    root.post(f"/api/companies/{cid}/deletion", expect=(409,))
    env.sql(f"UPDATE public.companies SET suspended_at = now() - interval '31 days' WHERE company_id = '{cid}'")
    root.post(f"/api/companies/{cid}/deletion")
    root.put(f"/api/companies/{cid}", json={"is_active": True}, expect=(400, 409))

    # The job empties each store; the device CA's revocation and the tracking store's collection are
    # the operator steps it waits on (docs/operations/user-management.md).
    deadline, status = time.monotonic() + DELETION_TIMEOUT, None
    while time.monotonic() < deadline:
        env.job("tenant-delete", "tenant-delete")
        status = root.get(f"/api/companies/{cid}/deletion").json()
        if status.get("completed_at"):
            break
        env.script("scripts/device-ca.sh", "revoke-tenant", cid)
        env.job("tenant-delete", "tracking-collect")
        time.sleep(30)
    assert status and status.get("completed_at"), f"deletion did not complete: {pretty(status)}"

    schema = f"tenant_{cid.replace('-', '_')}"
    assert env.sql(f"SELECT count(*) FROM pg_namespace WHERE nspname = '{schema}'") == "0"
    assert env.sql(f"SELECT count(*) FROM pg_roles WHERE rolname LIKE '%{cid.replace('-', '_')}'") == "0"
    tombstone = root.get(f"/api/companies/{cid}").json()
    assert tombstone["is_active"] is False, pretty(tombstone)
