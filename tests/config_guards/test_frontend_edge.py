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


def _locations():
    """(location line, [directives]) for each location block; plus the server-level directives."""
    server, blocks, current, depth = [], [], None, 0
    for line in TEMPLATE.splitlines():
        code = line.split("#", 1)[0].strip()
        if not code:
            continue
        if code.startswith("location ") and depth == 1:
            current = (code, [])
            blocks.append(current)
            if code.endswith("}"):  # one-line block
                current[1].append(code[code.index("{") + 1:-1].strip())
                current = None
                continue
        elif current is not None and depth == 2 and code != "}":
            current[1].append(code)
        elif depth == 1 and current is None:
            server.append(code)
        depth += code.count("{") - code.count("}")
        if depth == 1:
            current = None
    return server, blocks


def test_api_and_websocket_routes_forward_the_host_with_its_port():
    # The gateway's WebSocket Origin check compares Origin with the request host; behind compose's
    # :3443 the browser sends both with the port, so the edge must forward $http_host. A location
    # that sets any proxy_set_header inherits none from the server (it once sent its upstream name).
    server, blocks = _locations()
    checked = 0
    for loc, directives in blocks:
        if not any(re.search(r"proxy_pass http://\$(api_gateway|ml_service)", d) for d in directives):
            continue
        checked += 1
        own = [d for d in directives if d.startswith("proxy_set_header")]
        effective = own if own else [d for d in server if d.startswith("proxy_set_header")]
        assert "proxy_set_header Host $http_host;" in effective, f"{loc} forwards {effective}"
        assert any(d.startswith("proxy_set_header X-Forwarded-Proto") for d in effective), loc
    assert checked >= 10, f"only {checked} gateway/ML locations found — the parser is not reading"


GRAFANA_ROUTE = (FRONTEND / "grafana.conf.template").read_text()


def test_the_dashboards_route_is_rendered_only_when_its_upstream_is_named():
    # ADR-0016 dec 6: optional, so it is not in the template's required set, and the entrypoint
    # renders it with exactly its own variable (envsubst unrestricted would eat nginx's $vars).
    code = "\n".join(line for line in GRAFANA_ROUTE.splitlines() if not line.lstrip().startswith("#"))
    assert set(re.findall(r"\$\{(EDGE_\w+)\}", code)) == {"EDGE_GRAFANA", "EDGE_GRAFANA_ACCESS"}
    assert "EDGE_GRAFANA" not in _template_vars()
    assert 'if [ -n "${EDGE_GRAFANA:-}" ]' in ENTRYPOINT
    assert "include /etc/nginx/conf.d/edge-optional/*.conf;" in TEMPLATE
    dockerfile = (FRONTEND / "Dockerfile").read_text()
    assert "COPY grafana.conf.template /etc/nginx/edge/grafana.conf.template" in dockerfile
    assert "/etc/nginx/conf.d/edge-optional" in dockerfile


def test_the_dashboards_route_is_off_unless_the_operator_turns_it_on():
    assert compose_env()["EDGE_GRAFANA"] == ""
    assert "EDGE_GRAFANA" not in helm_env(["--set", "notebookHub.enabled=true"])


def test_the_dashboards_route_forwards_the_host_and_is_outside_the_spa_policy():
    assert "proxy_set_header Host $http_host;" in GRAFANA_ROUTE
    assert "proxy_set_header X-Forwarded-Proto https;" in GRAFANA_ROUTE
    assert "add_header" not in "\n".join(
        line for line in GRAFANA_ROUTE.splitlines() if not line.lstrip().startswith("#"))
    assert '~^/grafana/ "";' in TEMPLATE


def test_grafana_serves_from_the_path_the_edge_forwards():
    import yaml

    grafana = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["grafana"]
    env = grafana["environment"]
    assert env["GF_SERVER_ROOT_URL"].endswith("/grafana/")
    assert env["GF_SERVER_SERVE_FROM_SUB_PATH"] == "true"
    assert "/grafana/api/health" in " ".join(grafana["healthcheck"]["test"])
    assert any(p.startswith("127.0.0.1:") for p in grafana["ports"])


def test_the_dashboards_source_allowlist_is_optional_and_validated():
    assert compose_env()["EDGE_GRAFANA_ALLOW"] == ""
    assert "${EDGE_GRAFANA_ACCESS}" in GRAFANA_ROUTE
    assert "envsubst '${EDGE_GRAFANA} ${EDGE_GRAFANA_ACCESS}'" in ENTRYPOINT
    assert "deny all;" in ENTRYPOINT and "refusing to start: EDGE_GRAFANA_ALLOW" in ENTRYPOINT


def test_nginx_redirects_never_name_the_containers_listen_port():
    # /grafana and /jupyter without the slash were sent to https://<host>:8443/..., unpublished.
    server, _ = _locations()
    assert "absolute_redirect off;" in server
