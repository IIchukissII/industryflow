# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Notebooks are served from an origin of their own and entered by a ticket (ADR-0048): a browser
session mints one, the notebook origin spends it once for a notebook session that reaches the hub, the
platform's cookies open nothing there, the notebook origin can change nothing on the platform, and the
end of the platform session ends the notebook session."""
import uuid

import pytest

from harness import Secret


@pytest.fixture
def account(root, tenant):
    """An engineer of the run's tenant; the tenant's deletion removes it."""
    email = f"e2e-{tenant['run']}-notebooks-{uuid.uuid4().hex[:6]}@example.com"
    password = Secret(f"E2e-{uuid.uuid4().hex}")
    root.post("/api/users", json={"email": email, "password": password.reveal(), "role": "engineer",
                                  "company_id": tenant["company_id"]})
    return email, password


def test_a_ticket_enters_the_notebook_origin_once_and_the_session_ends_with_the_platforms(browser, account):
    browser.sign_in(*account)
    ticket = browser.ticket()
    entered = browser.enter(ticket)
    assert entered.status_code == 303 and entered.headers["location"] == "/", entered.text[:300]
    assert browser.notebooks.cookies.get("if_nb")
    # The hub signs the user on from the forwarded identity, with no second login.
    home = browser.notebooks.get("/jupyter/hub/home", follow_redirects=True)
    assert home.status_code == 200 and "/jupyter/hub/" in str(home.url), (home.status_code, home.url)
    assert browser.notebooks.cookies.get("jupyterhub-hub-login")
    # Spent.
    assert browser.enter(ticket).status_code == 401
    # Signing out of the platform signs out of the hub first, then ends the notebook session.
    assert browser.notebooks.get("/logout", follow_redirects=True).status_code == 200
    assert browser.page_post("/auth/logout").status_code == 204
    assert browser.notebooks.get("/jupyter/hub/home").status_code == 401


def test_entry_is_made_from_a_platform_page_only(browser, account):
    browser.sign_in(*account)
    assert browser.enter(browser.ticket(), origin="https://evil.example").status_code == 401
    assert browser.enter(browser.ticket(), origin=browser.notebook_origin).status_code == 401


def test_the_platforms_cookies_open_nothing_on_the_notebook_origin(browser, account, settings):
    browser.sign_in(*account)
    cookies = "; ".join(f"{k}={v}" for k, v in browser.platform.cookies.items())
    r = browser.notebooks.get("/jupyter/hub/home", headers={"Cookie": cookies})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/api/notebook-ticket", "/auth/refresh", "/auth/logout", "/api/equipment"])
def test_the_notebook_origin_changes_nothing_on_the_platform(browser, account, path):
    browser.sign_in(*account)
    r = browser.platform.post(path, headers={"Origin": browser.notebook_origin,
                                             "X-CSRF-Token": browser.platform.cookies.get("if_csrf", "")},
                              json={})
    assert r.status_code == 403 and r.json()["detail"] == "Cross-origin request refused", (r.status_code, r.text[:200])
    # The session is untouched.
    assert browser.platform.get("/users/me").status_code == 200
