# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The single host's memory budget (ADR-0018 dec 6, ADR-0009 dec 6): every long-running Compose service
holds a share of the host, and the notebook share is what they leave.

A service without a limit takes memory from every other when it grows, and a limit written as a fixed
amount is right for one host only. Both sides of the list are held: every long-running service takes
its limit from the budget, and the budget names no service Compose does not run.
"""
from __future__ import annotations

import importlib.util
import re
import tomllib

import yaml
from _repo import ROOT

COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text()
COMPOSE = yaml.safe_load(COMPOSE_TEXT)["services"]
SHARES = tomllib.loads((ROOT / "deploy" / "host-memory.toml").read_text())
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
# The reference host of docs/operations/capacity.md: 8 GB, of which the kernel shows this much.
REFERENCE_HOST_MIB = 7765

_spec = importlib.util.spec_from_file_location("host_memory", ROOT / "scripts" / "host-memory.py")
host_memory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(host_memory)


def _long_running():
    return {n for n, s in COMPOSE.items()
            if s.get("restart") not in (None, "no") and s.get("command") not in (True, "true", ["true"])}


def test_every_long_running_service_takes_its_limit_from_the_budget():
    for name in sorted(_long_running()):
        limit = str(COMPOSE[name].get("mem_limit", ""))
        assert limit == "${%s:?run scripts/host-memory.py}" % host_memory.limit_var(name), (
            f"{name}: mem_limit {limit!r} is not its share of the host")


def test_the_budget_names_exactly_the_long_running_services():
    assert set(SHARES["services"]) == _long_running()


def test_every_budget_variable_compose_asks_for_is_one_the_script_writes():
    asked = set(re.findall(r"\$\{([A-Z0-9_]+):\?run scripts/host-memory\.py\}", COMPOSE_TEXT))
    written = set(host_memory.budget(SHARES, REFERENCE_HOST_MIB))
    assert asked and not asked - written, f"compose asks for what the budget does not write: {sorted(asked - written)}"


def test_the_budget_fits_the_reference_host_with_a_notebook_share():
    values = host_memory.budget(SHARES, REFERENCE_HOST_MIB)
    assert int(values["NOTEBOOK_MEMORY_SHARE_MIB"]) >= REFERENCE_HOST_MIB * SHARES["notebook_min"] - 1


def test_the_environment_template_sets_no_budget_variable():
    written = set(host_memory.budget(SHARES, REFERENCE_HOST_MIB))
    set_there = {line.split("=", 1)[0] for line in ENV_EXAMPLE.splitlines() if re.match(r"^[A-Z0-9_]+=", line)}
    assert not written & set_there, f".env.example sets budget variables: {sorted(written & set_there)}"
