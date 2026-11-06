# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's life through deletion (ADR-0026 dec 10; ADR-0033): provisioned, used, suspended (its
users and sessions refused at once, its devices at the certificate edge within the tenant-status
stream's publish interval), its deletion refused until the suspension is old enough, then
removed from every store by the deletion job and the operator steps it waits on, leaving a tombstone.

The suspension's age is the one thing set from inside: the deletion floor is thirty days of wall
clock (ADR-0033 dec 7). The job also carries out what earlier runs left requested."""
import os
import sys
import time
import uuid

from harness import Secret, equipment, eventually, issue_device, pretty, reading, send_reading

# The shared contract is standard library only; the runner image reads it from the checkout.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "libs", "industryflow_contracts"))
from industryflow_contracts import tenant_status  # noqa: E402

DELETION_TIMEOUT = 1200
# A suspension reaches ingestion with the next publish of the stream; past a few intervals it never will.
REFUSAL_TIMEOUT = 3 * tenant_status.PUBLISH_INTERVAL_SECONDS


def test_a_tenant_is_provisioned_used_suspended_and_deleted_from_every_store(edge, root, env, settings):
    run = uuid.uuid4().hex[:8]
    cid = root.post("/api/companies", json={"company_name": f"e2e-life-{run}"}).json()["company_id"]
    email, password = f"e2e-life-{run}@example.com", Secret(f"E2e-{uuid.uuid4().hex}")
    root.post("/api/users", json={"email": email, "password": password.reveal(), "role": "admin",
                                  "company_id": cid})
    admin = edge.session(email, password)
    admin.get("/api/equipment")
    eid, sensors = equipment(admin, "cabinet", f"e2e-life-{run}", ["temp_root"])
    device = issue_device(env, settings["IF_E2E_COMPOSE_DIR"], cid, f"e2e-life-dev-{run}")
    r = send_reading(settings, device, reading(eid, sensors["temp_root"], 1.0))
    assert r.status_code == 202, r.text[:300]

    # Suspended: its users and their live sessions are refused at once, its devices once the
    # tenant-status stream carries the suspension to ingestion (ADR-0033 dec 3).
    root.put(f"/api/companies/{cid}", json={"is_active": False})
    admin.get("/api/equipment", expect=(401, 403))
    assert edge.login(email, password).status_code in (400, 401, 403)

    def refused():
        r = send_reading(settings, device, reading(eid, sensors["temp_root"], 2.0))
        assert r.status_code in (202, 403), r.text[:300]
        return r.status_code == 403 and r.json().get("detail") == "Tenant suspended"

    eventually(refused, timeout=REFUSAL_TIMEOUT, every=2, what="the suspended tenant's device is refused at ingestion")

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
