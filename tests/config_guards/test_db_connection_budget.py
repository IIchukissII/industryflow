# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""max_connections must cover the sum of the per-role CONNECTION LIMITs the init scripts set.

Both sides are lists that drift apart silently: a new role with its own limit, or a raised limit,
lets the roles together exhaust the server while each stays under its own ceiling.
"""

from __future__ import annotations

import re

from _repo import ROOT, load_yaml

INIT = ROOT / "infrastructure" / "timescaledb" / "init-scripts"
CONF = ROOT / "infrastructure" / "timescaledb" / "config" / "postgresql.conf"


def _role_limits() -> dict[str, int]:
    limits: dict[str, int] = {}
    # Scripts run in filename order, so a later migration's limit is the one in force.
    for script in sorted(INIT.iterdir()):
        for role, n in re.findall(r"ALTER ROLE (\w+) CONNECTION LIMIT (\d+)", script.read_text()):
            limits[role] = int(n)
    return limits


def _conf(name: str) -> int:
    m = re.search(rf"^{name}\s*=\s*(\d+)", CONF.read_text(), re.M)
    assert m, f"{name} is not set in postgresql.conf"
    return int(m.group(1))


def test_max_connections_covers_every_role_ceiling():
    limits = _role_limits()
    assert len(limits) >= 8, f"found only {sorted(limits)} — the guard is not reading the scripts"
    budget = sum(limits.values()) + _conf("superuser_reserved_connections")
    assert _conf("max_connections") > budget, (
        f"max_connections={_conf('max_connections')} does not exceed the role ceilings "
        f"{limits} + reserved = {budget}"
    )


def test_compose_sets_no_tuning_variables_the_image_ignores():
    env = load_yaml("docker-compose.yml")["services"]["timescaledb"]["environment"]
    ignored = [k for k in env if k.startswith("POSTGRES_") and k not in
               {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}]
    assert not ignored, f"the image reads none of {ignored}; tune postgresql.conf instead"
