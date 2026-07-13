# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fastapi-users auth surface, actually exercised (ADR-0004).

Every other test in this suite imports the service's own modules. NONE of them import `main`, so
until this file existed, `FastAPIUsers(...)`, the two authentication backends, the cookie transport
and all three generated routers were **never constructed by the test suite** — a fastapi-users major
bump could rewrite that API and the 25 green tests would not have noticed. That is precisely the
"green CI proves the parts we don't exercise still import" failure a dependency migration invites.

So this builds the real app and drives the routes fastapi-users generates:

  * a **422** on login/register means the router exists and its schema validation ran — the library's
    route and pydantic machinery is alive. A **404** would mean the bump silently dropped the route.
  * a **401** on /users/me means the auth dependency ran and refused, which is the whole point of it.

No database is contacted: config.py's settings are satisfied by conftest's env, and database.py
builds its engine lazily. These assertions are about the auth *surface*, not about persistence.
"""
import pytest
from fastapi.testclient import TestClient

import main
import users
from users import ACCESS_COOKIE


@pytest.fixture(scope="module")
def client():
    # raise_server_exceptions=False so a handler blowing up surfaces as a 500 we can assert on,
    # rather than as an exception that looks like a test error.
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "method, path, kwargs, expected",
    [
        # fastapi-users generates these three. The status is the evidence the router is real.
        ("POST", "/auth/jwt/login", {"data": {}}, 422),
        ("POST", "/auth/register", {"json": {}}, 422),
        ("GET", "/users/me", {}, 401),
    ],
)
def test_fastapi_users_routers_are_mounted_and_live(client, method, path, kwargs, expected):
    assert client.request(method, path, **kwargs).status_code == expected


def test_both_auth_backends_are_registered():
    """ADR-0004: a bearer backend for machine callers and a cookie backend for the browser session."""
    transports = {type(b.transport).__name__ for b in (users.auth_backend, users.cookie_auth_backend)}
    assert transports == {"BearerTransport", "CookieTransport"}


def test_session_cookie_keeps_its_adr_0004_properties():
    """
    The httpOnly session cookie is a decision (ADR-0004 dec 3), not a default — assert it rather
    than trust that a major bump preserved the transport's constructor semantics.
    """
    transport = users.cookie_auth_backend.transport
    assert transport.cookie_name == ACCESS_COOKIE == "if_access"
    assert transport.cookie_httponly is True
