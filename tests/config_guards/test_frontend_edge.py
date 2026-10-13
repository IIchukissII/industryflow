# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The frontend edge under both deployments.

The edge used to hardcode compose service names and Docker's resolver, so under the chart it could
not reach one upstream (the Services are release-scoped) and its Ingress path looped on a redirect
to https. Now each deployment names its upstreams; these guards read the template's variables and
check both deployments supply every one, with names that resolve where they run. CI additionally
runs `nginx -t` on the built image with each set (images.yml).
"""

from __future__ import annotations

import re
import sys

from _repo import ROOT, find, helm_template

sys.path.insert(0, str(ROOT / "scripts"))

from edge_env import compose_env, helm_env  # noqa: E402

FRONTEND = ROOT / "services" / "frontend"
TEMPLATE = (FRONTEND / "nginx.conf.template").read_text()
ENTRYPOINT = (FRONTEND / "docker-entrypoint-edge.sh").read_text()

# Defaulted by the entrypoint from the container's own nameservers; a deployment may override it.
OPTIONAL = {"EDGE_RESOLVER"}


def _template_vars() -> set[str]:
    code = "\n".join(line for line in TEMPLATE.splitlines() if not line.lstrip().startswith("#"))
    return set(re.findall(r"\$\{(EDGE_\w+)\}", code))


def test_the_entrypoint_substitutes_exactly_the_templates_variables():
    listed = set(re.search(r'^VARS="([^"]+)"', ENTRYPOINT, re.M).group(1).split())
    assert listed == _template_vars(), (
        f"template uses {sorted(_template_vars())}, entrypoint substitutes {sorted(listed)}"
    )


def test_no_nginx_variable_shares_a_name_with_an_edge_variable():
    # envsubst is limited to the listed names; this keeps it that way if one is ever lower-cased.
    assert all(v.isupper() for v in _template_vars())


def test_compose_supplies_every_upstream():
    missing = _template_vars() - OPTIONAL - set(compose_env())
    assert not missing, f"compose frontend lacks {sorted(missing)}"


def test_the_chart_supplies_every_upstream_as_a_service_it_renders():
    args = ["--set", "notebookHub.enabled=true"]
    env = helm_env(args)
    missing = _template_vars() - OPTIONAL - set(env)
    assert not missing, f"chart frontend lacks {sorted(missing)}"
    services = {
        f"{d['metadata']['name']}.default.svc.cluster.local:{p['port']}"
        for d in helm_template(*args) if d.get("kind") == "Service" for p in d["spec"]["ports"]
    }
    for key in _template_vars() - OPTIONAL:
        target = env[key].removeprefix("http://")
        assert target in services, f"{key}={env[key]} names no Service in the render"


def test_the_ingress_reaches_a_port_the_edge_serves_without_redirecting():
    docs = helm_template()
    ingress = find(docs, "Ingress", "if-industryflow")
    port = next(
        p["backend"]["service"]["port"]["number"]
        for r in ingress["spec"]["rules"] for p in r["http"]["paths"]
        if p["backend"]["service"]["name"].endswith("-frontend")
    )
    assert re.search(rf"^\s*listen {port};", TEMPLATE, re.M), f"the edge does not listen on {port}"
    # A plain listener behind a TLS-terminating Ingress must serve on X-Forwarded-Proto: https.
    assert '"~^http:https:"    0;' in TEMPLATE


def test_security_headers_are_set_only_at_server_level():
    # nginx drops inherited add_header in any location that declares its own.
    depth, offenders = 0, []
    for line in TEMPLATE.splitlines():
        code = line.split("#", 1)[0]
        if "add_header" in code and depth > 1:
            offenders.append(line.strip())
        depth += code.count("{") - code.count("}")
    assert not offenders, f"add_header inside a location: {offenders}"
    for header in ("Strict-Transport-Security", "Content-Security-Policy", "X-Content-Type-Options",
                   "X-Frame-Options", "Referrer-Policy"):
        assert f"add_header {header}" in TEMPLATE, header


def test_the_frontend_image_takes_no_dead_build_args():
    dockerfile = (FRONTEND / "Dockerfile").read_text()
    assert "REACT_APP_" not in dockerfile
    assert "REACT_APP_" not in (ROOT / "docker-compose.yml").read_text()
    assert "REACT_APP_" not in (ROOT / ".env.example").read_text()
