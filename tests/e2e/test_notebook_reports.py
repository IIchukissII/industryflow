# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An observer's notebook server serves rendered reports and no route that runs code (ADR-0011 dec 5):
the observer enters by a ticket (ADR-0048), the hub spawns the analytics server, it lands on its
reports, and the kernel, contents, file and terminal routes are absent."""
import re
import uuid

import pytest

from harness import Secret, eventually

SPAWN_SECONDS = 180
ABSENT = ["api/kernels", "api/sessions", "api/contents", "files/x.ipynb", "api/terminals",
          "api/kernelspecs", "api/nbconvert", "lab", "tree", "voila", "edit/x"]


@pytest.fixture
def observer(root, tenant):
    """An observer of the run's tenant; the tenant's deletion revokes its capabilities, which stops
    its server."""
    email = f"e2e-{tenant['run']}-observer-{uuid.uuid4().hex[:6]}@example.com"
    password = Secret(f"E2e-{uuid.uuid4().hex}")
    root.post("/api/users", json={"email": email, "password": password.reveal(), "role": "observer",
                                  "company_id": tenant["company_id"]})
    return email, password


def _running_prefix(browser):
    """The server's prefix, /jupyter/user/<name>/, once the hub's spawn reaches it."""
    r = browser.notebooks.get("/jupyter/hub/spawn", follow_redirects=True)
    m = re.search(r"/jupyter/user/[^/]+/", r.url.path)
    return m.group(0) if r.status_code == 200 and m else None


def test_an_observer_reaches_the_reports_and_no_route_that_runs_code(browser, observer):
    browser.sign_in(*observer)
    entered = browser.enter(browser.ticket())
    assert entered.status_code == 303, entered.text[:300]
    prefix = eventually(lambda: _running_prefix(browser), timeout=SPAWN_SECONDS, every=3,
                        what="the observer's server starts")
    landing = browser.notebooks.get(prefix, follow_redirects=True)
    assert landing.status_code == 200 and landing.url.path == prefix + "reports", landing.url
    for path in ABSENT:
        r = browser.notebooks.get(prefix + path, follow_redirects=True)
        assert r.status_code == 404, (path, r.status_code, r.url)
