# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An observer's notebook server serves rendered reports and no route that runs code (ADR-0011 dec 5):
the observer enters by a ticket (ADR-0048), the hub spawns the analytics server, its reports page
answers, and the kernel, contents, file and terminal routes are absent."""
import re
import time
import uuid

import pytest

from harness import Secret
from test_notebook_origin import Browser

SPAWN_SECONDS = 180


@pytest.fixture
def observer(root, tenant):
    """An observer of the run's tenant; the tenant's deletion removes it and its server."""
    email = f"e2e-{tenant['run']}-observer-{uuid.uuid4().hex[:6]}@example.com"
    password = Secret(f"E2e-{uuid.uuid4().hex}")
    root.post("/api/users", json={"email": email, "password": password.reveal(), "role": "observer",
                                  "company_id": tenant["company_id"]})
    return email, password


@pytest.fixture
def browser(settings):
    b = Browser(settings)
    yield b
    b.close()


def _server_prefix(browser) -> str:
    """Spawns the observer's server and returns its prefix, /jupyter/user/<name>/, once it answers."""
    deadline = time.monotonic() + SPAWN_SECONDS
    while time.monotonic() < deadline:
        r = browser.notebooks.get("/jupyter/hub/spawn", follow_redirects=True)
        m = re.search(r"(/jupyter/user/[^/]+/)", str(r.url.path))
        if r.status_code == 200 and m and "spawn-pending" not in str(r.url):
            return m.group(1)
        time.sleep(3)
    pytest.fail(f"the observer's server did not start in {SPAWN_SECONDS}s (last {r.status_code} {r.url})")


ABSENT = ["api/kernels", "api/sessions", "api/contents", "files/x.ipynb", "api/terminals",
          "api/kernelspecs", "api/nbconvert", "lab", "tree", "voila", "edit/x"]


def test_an_observer_reaches_the_reports_and_no_route_that_runs_code(browser, observer):
    browser.sign_in(*observer)
    entered = browser.enter(browser.ticket())
    assert entered.status_code == 303, entered.text[:300]
    prefix = _server_prefix(browser)
    landing = browser.notebooks.get(prefix, follow_redirects=True)
    assert landing.status_code == 200 and str(landing.url.path) == prefix + "reports", landing.url
    for path in ABSENT:
        r = browser.notebooks.get(prefix + path, follow_redirects=True)
        assert r.status_code == 404, (path, r.status_code, r.url)
