# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The dashboards at the edge sign on from a platform superuser's session (ADR-0016 dec 7): no session
goes to the platform's login and comes back, a tenant's administrator is refused, the superuser is
signed in as an editor under their platform id, an assertion the client sends is overwritten, a write a
browser did not send from this origin is refused, and an assertion without the gateway's signature
signs no one in, even from inside the platform network. Where the deployment has not turned the route on (ADR-0016 dec 6), the journey
checks that the edge serves no dashboards at all."""
import json

import pytest

PROBE = "/grafana/api/user"
HEADER = "X-IF-Dashboards-Assertion"

# Run inside the gateway's container: it is on the dashboards' network and carries the signing
# library. argv[1] is the key to sign with ("real" or "forged"), argv[2] the login; prints the
# dashboards' status.
SIGN_AND_ASK = r"""
import json, sys, time, urllib.error, urllib.request
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt
if sys.argv[1] == "real":
    key = open("/run/dashboards-signing/signing.pem").read()
else:
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
now = int(time.time())
token = jwt.encode({"iss": "industryflow-api-gateway", "aud": "industryflow-dashboards",
                    "sub": sys.argv[2], "role": "Editor",
                    "iat": now, "nbf": now, "exp": now + 60}, key, algorithm="ES256")
req = urllib.request.Request("http://grafana:3000/grafana/api/user", headers={"%s": token})
try:
    print(urllib.request.urlopen(req, timeout=10).status)
except urllib.error.HTTPError as e:
    print(e.code)
""" % HEADER


def _routed(edge) -> bool:
    r = edge.anonymous(PROBE, follow_redirects=False)
    return not (r.status_code == 200 and r.headers.get("content-type", "").startswith("text/html"))


def _inside(env, which: str, login: str) -> int:
    return int(env.run("docker", "exec", "-i", "industryflow-api-gateway", "python", "-", which, login,
                       input=SIGN_AND_ASK).strip())


def _forged_assertion() -> str:
    """A well-formed ES256 assertion for another login, under a signature no key made."""
    import base64
    import os
    import time

    def part(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(time.time())
    claims = {"iss": "industryflow-api-gateway", "aud": "industryflow-dashboards", "sub": "if-forged",
              "role": "Editor", "iat": now, "nbf": now, "exp": now + 60}
    return ".".join([part(json.dumps({"alg": "ES256", "typ": "JWT"}).encode()),
                     part(json.dumps(claims).encode()), part(os.urandom(64))])


@pytest.fixture(scope="module")
def login(root) -> str:
    return "if-" + root.get("/users/me").json()["id"]


@pytest.fixture(scope="module")
def routed(edge) -> bool:
    return _routed(edge)


def test_without_the_route_the_edge_serves_no_dashboards(edge, routed):
    # A journey never skips (IF_E2E_REQUIRE): with the route off, what is checked is that it is off.
    if routed:
        return
    r = edge.anonymous(PROBE, follow_redirects=False)
    assert "grafana" not in r.text.lower()[:2000]


def test_no_session_goes_to_the_platform_login_and_back(edge, routed):
    if not routed:
        return
    page = edge.anonymous("/grafana/d/x?orgId=1", follow_redirects=False)
    assert page.status_code == 302 and page.headers["location"] == "/login?next=/grafana/d/x?orgId=1", (
        page.status_code, page.headers)
    # The dashboards' own API calls get the 401, so their page reloads through the edge.
    assert edge.anonymous(PROBE, follow_redirects=False).status_code == 401


def test_a_client_supplied_assertion_is_overwritten(root, tenant, login, routed):
    if not routed:
        return
    forged = {HEADER: _forged_assertion()}
    tenant["admin"].get(PROBE, headers=forged, expect=(403,))
    assert root.get(PROBE, headers=forged).json()["login"] == login


def test_a_tenant_administrator_is_refused(tenant, routed):
    if not routed:
        return
    tenant["admin"].get(PROBE, expect=(403,))


def test_the_superuser_is_signed_in_as_an_editor_under_their_platform_id(root, login, routed):
    if not routed:
        return
    me = root.get(PROBE).json()
    assert me["login"] == login, me
    assert me.get("isGrafanaAdmin") is False, me
    orgs = root.get("/grafana/api/user/orgs").json()
    assert [o["role"] for o in orgs] == ["Editor"], json.dumps(orgs)


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_write_a_browser_did_not_send_from_this_origin_is_refused(root, routed, site):
    if not routed:
        return
    root.post("/grafana/api/search/sorting", headers={"Sec-Fetch-Site": site, "Origin": "https://evil.example"},
              json={}, expect=(403,))


def test_only_the_gateways_signature_signs_in_from_inside_the_network(env, login, routed):
    if not routed:
        return
    # The positive control from the same place proves the refusal is the signature's, not the route's.
    assert _inside(env, "real", login) == 200
    assert _inside(env, "forged", login) == 401
