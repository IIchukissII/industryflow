# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sign-in limits by source, at the deployed edge (ADR-0004 dec 10, ADR-0026 dec 10).

Two sources the edge tells apart: the runner, through the published port, and a client attached to
the platform network that reaches the edge directly with its own address. From that second source an
account is slowed by the gateway and then the source itself by the edge, across accounts and on both
sign-in paths; the runner signs the slowed account's owner in throughout, and the second source is
let in again once the edge's bucket refills. The limit's rate and burst are read from the edge as
deployed, the one place they are set. The runner's own source is never slowed, so later journeys sign
in as before.
"""
import json
import re
import time
import uuid
from urllib.parse import urlparse

import pytest

from harness import Secret

NETWORK = "industryflow-network"
EDGE_CONTAINER = "industryflow-frontend"
PATHS = ("/auth/jwt/login", "/auth/login")

# The second source: each attempt as {path, username, password}; prints one JSON line per answer.
CLIENT = r"""
import json, ssl, sys, urllib.error, urllib.parse, urllib.request
plan = json.load(sys.stdin)
ctx = ssl.create_default_context(cafile=plan["ca"])
out = []
for a in plan["attempts"]:
    body = urllib.parse.urlencode({"username": a["username"], "password": a["password"]}).encode()
    req = urllib.request.Request(plan["base"] + a["path"], data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            status, headers, text = r.status, r.headers, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status, headers, text = e.code, e.headers, e.read().decode(errors="replace")
    out.append({"status": status, "json": (headers.get("Content-Type") or "").startswith("application/json"),
                "retry_after": headers.get("Retry-After"), "body": text[:200]})
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def limit(env):
    """The edge's per-source sign-in limit as deployed: on, its rate per minute and its burst."""
    config = env.run("docker", "exec", EDGE_CONTAINER, "nginx", "-T")
    assert re.search(r'map "on" \$edge_sign_in_source', config), "the per-source sign-in limit is off at this edge"
    rate = int(re.search(r"limit_req_zone \$edge_sign_in_source zone=edge_sign_in:\S+ rate=(\d+)r/m;", config).group(1))
    bursts = {int(b) for b in re.findall(r"limit_req zone=edge_sign_in burst=(\d+)", config)}
    paths = set(re.findall(r"location = (/auth/(?:jwt/)?login) +\{ limit_req zone=edge_sign_in", config))
    assert paths == set(PATHS), f"the limit guards {paths}, not both sign-in paths"
    [burst] = bursts
    return {"rate": rate, "burst": burst}


@pytest.fixture(scope="module")
def second_source(env, settings, tenant):
    """Sign-in attempts from one client on the platform network, as the edge sees it: its own address,
    the same for every attempt (one container for the journey, removed at the end)."""
    host = urlparse(settings["IF_E2E_EDGE"]).hostname
    ip = env.run("docker", "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{NETWORK}").IPAddress}}}}',
                 EDGE_CONTAINER).strip()
    assert ip, "the edge has no address on the platform network"
    name = f"e2e-sign-in-source-{tenant['run']}"
    env.run("docker", "run", "-d", "--rm", "--name", name, "--network", NETWORK, "--add-host", f"{host}:{ip}",
            "-v", f"{settings['IF_E2E_CA']}:{settings['IF_E2E_CA']}:ro", "--entrypoint", "sleep",
            "industryflow-e2e", "900")

    def attempt(attempts):
        plan = {"base": f"https://{host}:8443", "ca": settings["IF_E2E_CA"], "attempts": attempts}
        out = env.run("docker", "exec", "-i", name, "python3", "-c", CLIENT, input=json.dumps(plan))
        return json.loads(out.strip().splitlines()[-1])

    try:
        yield attempt
    finally:
        env.run("docker", "rm", "-f", name)


@pytest.fixture(scope="module")
def owner(root, tenant):
    """An account of the run's tenant whose sign-in the second source slows."""
    email, password = f"e2e-{tenant['run']}-owner@example.com", Secret(f"E2e-{uuid.uuid4().hex}")
    root.post("/api/users", json={"email": email, "password": password.reveal(), "role": "engineer",
                                  "company_id": tenant["company_id"]})
    return email, password


def _edge_refused(answer):
    # The edge's refusal is its own page: no JSON, no Retry-After. The gateway's 429 is JSON with one.
    return answer["status"] == 429 and not answer["json"] and answer["retry_after"] is None


def test_one_source_is_slowed_and_the_other_signs_in(edge, second_source, owner, limit):
    email, password = owner

    # The gateway slows the account at the second source: wrong passwords until its own 429. Each
    # attempt also spends one of the second source's edge tokens.
    spent = 7
    answers = second_source([{"path": "/auth/jwt/login", "username": email, "password": "wrong"}] * spent)
    slowed = [a for a in answers if a["status"] == 429]
    assert slowed and slowed[0]["json"] and slowed[0]["retry_after"], answers
    assert not any(_edge_refused(a) for a in answers), "the edge refused before the gateway slowed the account"

    # The owner, from the runner's source, is not slowed (ADR-0004 dec 10).
    assert edge.login(email, password).status_code == 200

    # The edge slows the second source across accounts, on both paths alike: its bucket holds the burst,
    # of which the account's attempts above already spent some.
    unknown = [{"path": PATHS[i % 2], "username": f"nobody-{uuid.uuid4().hex[:8]}@example.invalid",
                "password": "wrong"} for i in range(limit["burst"] + 5)]
    answers = second_source(unknown)
    first = next(i for i, a in enumerate(answers) if _edge_refused(a))
    # The bucket admits one request beyond the burst; between the two calls at most a token or two
    # returns (one every 60/rate s).
    remaining = limit["burst"] + 1 - spent
    assert remaining - 1 <= first <= remaining + 2, (first, remaining, answers)
    assert all(_edge_refused(a) for a in answers[first:]), "a refused source was let in before its bucket refilled"
    assert {unknown[i]["path"] for i in range(first, len(answers))} == set(PATHS), "only one path was limited"

    # The runner's source is a different bucket: the owner still signs in.
    assert edge.login(email, password).status_code == 200

    # Nothing is locked: once one token has returned, the second source is answered by the gateway again.
    time.sleep(60 / limit["rate"] + 2)
    [again] = second_source([{"path": "/auth/jwt/login", "username": "nobody@example.invalid", "password": "wrong"}])
    assert not _edge_refused(again), again
