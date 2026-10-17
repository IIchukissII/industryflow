# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The notebook hub checks its authoring-role setting against the platform's roles; its copy of that
set must equal the API gateway's, or a valid role is refused and a retired one still accepted."""

from __future__ import annotations

import ast
import re

from _repo import ROOT, load_yaml

HUB_IDENTITY = ROOT / "services" / "notebook_hub" / "identity.py"
GATEWAY_SCHEMAS = ROOT / "services" / "api_gateway" / "schemas.py"


def _hub_constant(name: str):
    for node in ast.parse(HUB_IDENTITY.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return node.value
    raise AssertionError(f"{name} not found in {HUB_IDENTITY}")


def _hub_frozenset(name: str) -> set[str]:
    call = _hub_constant(name)
    return set(ast.literal_eval(call.args[0]))


def _gateway_roles() -> set[str]:
    match = re.search(r"^Role = Literal\[([^\]]*)\]", GATEWAY_SCHEMAS.read_text(), re.M)
    assert match, f"no Role literal in {GATEWAY_SCHEMAS}"
    return set(ast.literal_eval(f"[{match.group(1)}]"))


def test_hub_platform_roles_equal_the_gateway_roles():
    assert _hub_frozenset("PLATFORM_ROLES") == _gateway_roles()


def test_every_shipped_authoring_role_list_names_platform_roles_only():
    roles = _gateway_roles()
    assert _hub_frozenset("DEFAULT_AUTHORING_ROLES") <= roles
    compose = (ROOT / "docker-compose.yml").read_text()
    env = (ROOT / ".env.example").read_text()
    helm = load_yaml("deploy/helm/industryflow/values.yaml")["notebookHub"]["authoringRoles"]
    shipped = {
        "docker-compose.yml": re.search(r"\$\{NOTEBOOK_AUTHORING_ROLES:-([^}]*)\}", compose).group(1),
        ".env.example": re.search(r"^NOTEBOOK_AUTHORING_ROLES=(.*)$", env, re.M).group(1),
        "values.yaml": helm,
    }
    for where, value in shipped.items():
        listed = {r.strip() for r in value.split(",") if r.strip()}
        assert listed and listed <= roles, f"{where} lists {sorted(listed)}; platform roles: {sorted(roles)}"
