# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An access token stops working when its session ends, not when it expires (ADR-0004 dec 2): after a
logout, and after a password change, the gateway and the ML service both refuse the access token the
ended session was issued, while a session that did not end keeps working; repeated wrong passwords
slow one account, and a refresh token reused after its rotation ends its session."""
import time
import uuid

import pytest

from harness import Edge, Secret, eventually

# One route answered by the gateway and one by the ML service, each re-reading the account.
PROBES = ("/api/equipment", "/api/feature-configs")


@pytest.fixture
def account(root, tenant):
    """A user of its own per journey (a journey may change its password); the tenant's deletion removes it."""
    email = f"e2e-{tenant['run']}-session-{uuid.uuid4().hex[:6]}@example.com"
    password = Secret(f"E2e-{uuid.uuid4().hex}")
    root.post("/api/users", json={"email": email, "password": password.reveal(), "role": "engineer",
                                  "company_id": tenant["company_id"]})
    return email, password


def _sign_in(settings, email, password):
    """A session of its own, on its own client: the shared one would carry this session's cookies."""
    edge = Edge(settings["IF_E2E_EDGE"], settings["IF_E2E_CA"])
    r = edge.client.post("/auth/login", data={"username": email, "password": password.reveal()})
    assert r.status_code == 200, (r.status_code, r.text[:300])
    edge.client.cookies.clear()
    return edge, r.json()


def _status(edge, token, path):
    return edge.client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code


def test_a_logged_out_sessions_access_token_is_refused_everywhere(settings, account):
    ended, pair = _sign_in(settings, *account)
    other, kept = _sign_in(settings, *account)
    with ended, other:
        assert [_status(ended, pair["access_token"], p) for p in PROBES] == [200, 200]
        r = ended.client.post("/auth/logout", json={"refresh_token": pair["refresh_token"]})
        assert r.status_code == 204, r.text[:300]
        assert [_status(ended, pair["access_token"], p) for p in PROBES] == [401, 401]
        assert [_status(other, kept["access_token"], p) for p in PROBES] == [200, 200]


def test_a_password_change_refuses_every_earlier_access_token(settings, account):
    email, password = account
    edge, pair = _sign_in(settings, email, password)
    with edge:
        token = pair["access_token"]
        new = Secret(f"E2e-{uuid.uuid4().hex}")
        r = edge.client.patch("/users/me", json={"password": new.reveal()},
                              headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text[:300]
        assert [_status(edge, token, p) for p in PROBES] == [401, 401]
    # A token issued within the clock-skew allowance after the end counts as issued before it, so a
    # new sign-in works once the allowance has passed (industryflow_contracts.sessions).
    def signed_in_again():
        fresh, again = _sign_in(settings, email, new)
        with fresh:
            return [_status(fresh, again["access_token"], p) for p in PROBES] == [200, 200]

    eventually(signed_in_again, timeout=20, every=1, what="a new sign-in after the password change")


def test_the_access_token_only_logout_ends_its_session(settings, account):
    """The fastapi-users login issues no refresh token; its logout still ends the token's session."""
    email, password = account
    edge = Edge(settings["IF_E2E_EDGE"], settings["IF_E2E_CA"])
    with edge:
        r = edge.client.post("/auth/jwt/login", data={"username": email, "password": password.reveal()})
        assert r.status_code == 200, r.text[:300]
        edge.client.cookies.clear()
        token = r.json()["access_token"]
        assert [_status(edge, token, p) for p in PROBES] == [200, 200]
        r = edge.client.post("/auth/jwt/logout", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (200, 204), r.text[:300]
        assert [_status(edge, token, p) for p in PROBES] == [401, 401]


def test_wrong_passwords_slow_that_account_and_no_other(settings, account, root, tenant):
    """ADR-0004 dec 10: from the fifth failure the account waits, the right password included; another
    account signs in as before."""
    email, password = account
    with Edge(settings["IF_E2E_EDGE"], settings["IF_E2E_CA"]) as edge:
        for _ in range(5):
            r = edge.client.post("/auth/login", data={"username": email, "password": "wrong-password"})
            assert r.status_code == 400, r.text[:200]
        r = edge.client.post("/auth/login", data={"username": email, "password": password.reveal()})
        assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1, (r.status_code, r.headers)
    other_email, other_password = _another(root, tenant)
    with _sign_in(settings, other_email, other_password)[0]:
        pass


def test_a_refresh_token_reused_after_the_grace_ends_its_session(settings, account):
    """ADR-0004 dec 2: presented again at once (another tab), the rotated token gets the successor;
    presented again after the grace, it ends the session for every holder."""
    edge, pair = _sign_in(settings, *account)
    with edge:
        rotated = edge.client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        assert rotated.status_code == 200, rotated.text[:200]
        again = edge.client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        assert again.status_code == 200 and again.json()["refresh_token"] == rotated.json()["refresh_token"]
        edge.client.cookies.clear()
        time.sleep(11)  # past the grace (ROTATION_GRACE_SECONDS, 10 s)
        reused = edge.client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        assert reused.status_code == 401, reused.text[:200]
        edge.client.cookies.clear()
        assert edge.client.post("/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 401
        assert [_status(edge, rotated.json()["access_token"], p) for p in PROBES] == [401, 401]


def _another(root, tenant):
    email = f"e2e-{tenant['run']}-other-{uuid.uuid4().hex[:6]}@example.com"
    password = Secret(f"E2e-{uuid.uuid4().hex}")
    root.post("/api/users", json={"email": email, "password": password.reveal(), "role": "engineer",
                                  "company_id": tenant["company_id"]})
    return email, password
