# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An account held to a password change reads itself, changes its password and signs out, and nothing
more, at the gateway and the ML service alike; once it has its own password it is an ordinary account
(ADR-0004 dec 11). A platform that has accounts gets no first-run account. The first run on an
empty platform itself is proved at every compose-smoke start."""
import uuid

from harness import Edge, Secret, eventually

PROBES = ("/api/equipment", "/api/feature-configs")


def _sign_in(settings, email, password):
    edge = Edge(settings["IF_E2E_EDGE"], settings["IF_E2E_CA"])
    r = edge.client.post("/auth/login", data={"username": email, "password": password})
    edge.client.cookies.clear()
    return edge, r


def test_a_held_account_must_change_its_password_before_anything_else(settings, env, root, tenant):
    email = f"e2e-{tenant['run']}-held-{uuid.uuid4().hex[:6]}@example.com"
    first = Secret(f"E2e-{uuid.uuid4().hex}")
    created = root.post("/api/users", json={"email": email, "password": first.reveal(), "role": "engineer",
                                            "company_id": tenant["company_id"]}).json()
    env.sql(f"UPDATE public.\"user\" SET password_change_required = true WHERE id = '{created['id']}'")
    edge, r = _sign_in(settings, email, first.reveal())
    with edge:
        assert r.status_code == 200, r.text[:200]
        bearer = {"Authorization": f"Bearer {r.json()['access_token']}"}
        for path in PROBES:
            refused = edge.client.get(path, headers=bearer)
            assert refused.status_code == 403 and refused.json()["detail"] == "PASSWORD_CHANGE_REQUIRED", (path, refused.text)
        me = edge.client.get("/users/me", headers=bearer)
        assert me.status_code == 200 and me.json()["password_change_required"] is True
        kept = edge.client.patch("/users/me", headers=bearer, json={"password": first.reveal()})
        assert kept.status_code == 400, kept.text[:200]
        new = Secret(f"E2e-{uuid.uuid4().hex}")
        changed = edge.client.patch("/users/me", headers=bearer, json={"password": new.reveal()})
        assert changed.status_code == 200, changed.text[:200]

    def released():
        again, r = _sign_in(settings, email, new.reveal())
        with again:
            if r.status_code != 200:
                return False
            bearer = {"Authorization": f"Bearer {r.json()['access_token']}"}
            me = again.client.get("/users/me", headers=bearer).json()
            return not me["password_change_required"] and [
                again.client.get(p, headers=bearer).status_code for p in PROBES] == [200, 200]

    eventually(released, timeout=20, every=1, what="the account after its own password")


def test_a_platform_with_accounts_gets_no_first_run_account(env):
    count = env.sql("SELECT count(*) FROM public.\"user\" WHERE email = 'admin@industryflow.local' "
                    "AND password_change_required").strip()
    assert count == "0", count
