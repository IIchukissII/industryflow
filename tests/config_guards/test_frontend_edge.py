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

# Defaulted by the entrypoint (the container's own nameservers; the sign-in limit on); a deployment may
# override them.
OPTIONAL = {"EDGE_RESOLVER", "EDGE_SIGN_IN_PER_SOURCE"}


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


# The dashboards' sign-on (ADR-0016 dec 7) spans three artifacts that each name the same values: the
# gateway route that signs the assertion, the edge route that asks for it and forwards it, and the
# dashboards' configuration that verifies it. A mismatch in any one fails closed or, worse, silently
# leaves the dashboards' own login as the only door.
ASSERTION_ROUTER = (ROOT / "services" / "api_gateway" / "routers" / "dashboards_assertion.py").read_text()


def _router_constant(name: str) -> str:
    return re.search(rf'^{name} = "([^"]+)"', ASSERTION_ROUTER, re.M).group(1)


def _compose_services():
    import yaml

    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def _volume_names(service) -> set[str]:
    return {v.split(":")[0] for v in service.get("volumes", []) if isinstance(v, str)}


def test_the_edge_asks_the_gateway_route_that_signs_the_assertion():
    path = _router_constant("PATH")
    assert f"proxy_pass http://$api_gateway{path};" in GRAFANA_ROUTE
    assert "location = /_dashboards_assertion {\n    internal;" in GRAFANA_ROUTE
    assert "auth_request /_dashboards_assertion;" in GRAFANA_ROUTE
    assert "proxy_set_header Cookie $http_cookie;" in GRAFANA_ROUTE


def test_the_edge_never_routes_the_gateways_internal_paths():
    # The assertion must never reach the browser: no edge location may forward /internal/.
    assert _router_constant("PATH").startswith("/internal/")
    for text in (TEMPLATE, GRAFANA_ROUTE):
        prefixes = re.findall(r"^\s*location\s+(?:=\s+|\^~\s+)?(/[^\s{]*)", text, re.M)
        regexes = re.findall(r"^\s*location\s+~\*?\s+(\S+)", text, re.M)
        assert not [x for x in regexes if "internal" in x], regexes
        assert prefixes, "no locations found — the parser is not reading"
        for prefix in prefixes:
            assert not ("/internal/".startswith(prefix) and prefix != "/") and \
                not prefix.startswith("/internal"), f"edge location {prefix} reaches /internal/"
    root = re.search(r"location / \{(.*?)\n    \}", TEMPLATE, re.S)
    assert root and "proxy_pass" not in root.group(1), "the edge's catch-all must not proxy"


def test_the_assertion_travels_under_one_header_name():
    header = _router_constant("ASSERTION_HEADER")
    upstream_var = "$upstream_http_" + header.lower().replace("-", "_")
    assert f"auth_request_set $if_dashboards_assertion {upstream_var};" in GRAFANA_ROUTE
    # Set unconditionally, so whatever the client sent under this name is overwritten.
    assert f"proxy_set_header {header} $if_dashboards_assertion;" in GRAFANA_ROUTE
    env = _compose_services()["grafana"]["environment"]
    assert env["GF_AUTH_JWT_HEADER_NAME"] == header


def test_the_dashboards_verify_what_the_gateway_signs():
    import json

    env = _compose_services()["grafana"]["environment"]
    assert env["GF_AUTH_JWT_ENABLED"] == "true"
    assert json.loads(env["GF_AUTH_JWT_EXPECT_CLAIMS"]) == {
        "iss": _router_constant("ISSUER"), "aud": _router_constant("AUDIENCE")}
    assert env["GF_AUTH_JWT_ROLE_ATTRIBUTE_PATH"] == "role"
    assert env["GF_AUTH_JWT_ROLE_ATTRIBUTE_STRICT"] == "true"
    assert env["GF_AUTH_JWT_ALLOW_ASSIGN_GRAFANA_ADMIN"] == "false"
    assert env["GF_AUTH_JWT_URL_LOGIN"] == "false"
    assert _router_constant("ROLE") in {"Viewer", "Editor"}
    # The login is the platform user's id, in a namespace of its own; no e-mail is mapped, because the
    # dashboards match an e-mail against their existing accounts (the administrator's) first.
    assert env["GF_AUTH_JWT_USERNAME_CLAIM"] == "sub"
    assert "GF_AUTH_JWT_EMAIL_CLAIM" not in env and "GF_AUTH_JWT_EMAIL_ATTRIBUTE_PATH" not in env
    assert '"sub": f"{LOGIN_PREFIX}{user.id}",' in ASSERTION_ROUTER
    assert '"email"' not in ASSERTION_ROUTER


def test_only_requests_the_per_request_check_covers_reach_the_dashboards():
    # A websocket is checked once, at its upgrade, and then outlives a logout or a demotion.
    env = _compose_services()["grafana"]["environment"]
    assert env["GF_LIVE_MAX_CONNECTIONS"] == "0"
    route = GRAFANA_ROUTE[GRAFANA_ROUTE.index("location /grafana/ {"):]
    assert "Upgrade" not in route.split("\n}")[0]


def test_a_write_a_browser_did_not_send_from_this_origin_is_refused():
    # The dashboards hold no session, so their own CSRF check never runs; the edge refuses instead.
    assert 'map "$request_method:$http_sec_fetch_site:$http_origin" $edge_dashboards_foreign_write {' in TEMPLATE
    m = re.search(r"\$edge_dashboards_foreign_write \{(.*?)\n\}", TEMPLATE, re.S).group(1)
    assert re.search(r"^\s*default\s+1;", m, re.M)
    route = GRAFANA_ROUTE[GRAFANA_ROUTE.index("location /grafana/ {"):]
    check = route.index("if ($edge_dashboards_foreign_write) {\n        return 403;")
    assert check < route.index("auth_request /_dashboards_assertion;")
    assert _compose_services()["grafana"]["environment"]["GF_SECURITY_CSRF_ALWAYS_CHECK"] == "true"


def test_a_visitor_without_a_session_comes_back_to_the_dashboards():
    assert "return 302 /login?next=$request_uri;" in GRAFANA_ROUTE
    returns = (ROOT / "services" / "frontend" / "src" / "services" / "returnPath.js").read_text()
    assert "/^\\/grafana\\/" in returns


def test_the_signing_key_reaches_the_gateway_and_only_its_public_half_the_dashboards():
    services = _compose_services()
    init = services["dashboards-key-init"]
    signing, verify = ("dashboards-signing-key", "dashboards-verify-key")
    assert {signing, verify} <= _volume_names(init)
    gateway, grafana = services["api-gateway"], services["grafana"]
    key_file = gateway["environment"]["DASHBOARDS_SIGNING_KEY_FILE"]
    assert any(v.startswith(f"{signing}:") and key_file.startswith(v.split(":")[1] + "/")
               and v.endswith(":ro") for v in gateway["volumes"])
    public = grafana["environment"]["GF_AUTH_JWT_KEY_FILE"]
    assert any(v.startswith(f"{verify}:") and public.startswith(v.split(":")[1] + "/")
               and v.endswith(":ro") for v in grafana["volumes"])
    assert init["image"] == gateway["image"] and init["build"] == gateway["build"]
    assert init["network_mode"] == "none"
    assert init["command"][:2] == ["python", "dashboards_key.py"]
    assert init["command"][-1] == "10001"
    gateway_dockerfile = (ROOT / "services" / "api_gateway" / "Dockerfile").read_text()
    assert "USER 10001" in gateway_dockerfile and "COPY . ." in gateway_dockerfile
    holders = [name for name, svc in services.items() if signing in _volume_names(svc)]
    assert sorted(holders) == ["api-gateway", "dashboards-key-init"], holders
    for name in ("api-gateway", "grafana"):
        dep = services[name]["depends_on"]["dashboards-key-init"]
        assert dep["condition"] == "service_completed_successfully"



def test_password_sign_in_is_limited_per_source_on_both_routes():
    """ADR-0004 dec 10: both sign-in routes carry the limit, keyed by the client address only where
    the deployment says the edge sees it; the chart (behind an Ingress) says it does not."""
    for route in ("/auth/login", "/auth/jwt/login"):
        line = next(line for line in TEMPLATE.splitlines() if line.strip().startswith(f"location = {route} "))
        assert "limit_req zone=edge_sign_in" in line and "limit_req_status 429" in line, line
    assert re.search(r"limit_req_zone \$edge_sign_in_source zone=edge_sign_in:", TEMPLATE)
    assert re.search(r'map "\$\{EDGE_SIGN_IN_PER_SOURCE\}" \$edge_sign_in_source \{\s*on\s+\$binary_remote_addr;', TEMPLATE)
    assert compose_env().get("EDGE_SIGN_IN_PER_SOURCE") == "on"
    assert helm_env([]).get("EDGE_SIGN_IN_PER_SOURCE") == "off"
    assert 'on|off) ;;' in ENTRYPOINT
