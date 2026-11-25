# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The single host's memory budget (ADR-0018 dec 6, ADR-0009 dec 6): every long-running Compose service
holds a part of the platform's measured need at its ingest tier, and the notebook share is what the
platform leaves of the host.

A service without a limit takes memory from every other when it grows, and a limit that is a fraction of
the host is wrong on every host but one: a service's memory follows the software and the load. Both sides
of the list are held: every long-running service takes its limit from the budget, and the budget names no
service Compose does not run.
"""
from __future__ import annotations

import datetime
import importlib.util
import re
import tomllib

import pytest
import yaml
from _repo import ROOT

COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text()
COMPOSE = yaml.safe_load(COMPOSE_TEXT)["services"]
SHARES = tomllib.loads((ROOT / "deploy" / "host-memory.toml").read_text())
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
# A host large enough for every tier, where the whole budget is written.
LARGE_HOST_MIB = 64 * 1024
LARGE_SWAP_MIB = 64 * 1024

_spec = importlib.util.spec_from_file_location("host_memory", ROOT / "scripts" / "host-memory.py")
host_memory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(host_memory)


def _long_running():
    return {n for n, s in COMPOSE.items()
            if s.get("restart") not in (None, "no") and s.get("command") not in (True, "true", ["true"])}


def test_every_long_running_service_takes_its_limits_from_the_budget():
    for name in sorted(_long_running()):
        for key, var in (("mem_limit", host_memory.limit_var(name)), ("memswap_limit", host_memory.swap_limit_var(name))):
            limit = str(COMPOSE[name].get(key, ""))
            assert limit == "${%s:?run scripts/host-memory.py}" % var, f"{name}: {key} {limit!r} is not its part of the budget"


def test_the_budget_names_exactly_the_long_running_services():
    assert set(SHARES["services"]) == _long_running()


@pytest.mark.parametrize("part", ["resident", "cold"])
def test_the_weights_are_the_whole_platform(part):
    total = sum(float(s[part]) for s in SHARES["services"].values())
    assert abs(total - 1.0) < 0.002, f"{part} weights sum to {total}"


def test_the_reserve_is_a_dated_measurement():
    assert isinstance(SHARES["reserve"]["measured"], datetime.date)
    assert int(SHARES["reserve"]["host_mib"]) > 0 and int(SHARES["reserve"]["one_shot_mib"]) >= 0


def test_every_tier_is_a_dated_measurement():
    """A tier's need is measured, never chosen: on which host, at what rate, when."""
    assert SHARES["tier"] in SHARES["tiers"]
    for name, tier in SHARES["tiers"].items():
        assert int(tier["resident_mib"]) > 0 and int(tier["cold_mib"]) >= 0, name
        assert int(tier["reference_host_mib"]) > 0, name
        assert int(tier["readings_per_second"]) > 0, name
        assert isinstance(tier["measured"], datetime.date), name


@pytest.mark.parametrize("tier", sorted(SHARES["tiers"]))
def test_compose_asks_for_exactly_the_variables_the_script_writes(tier):
    """Both sides: a variable compose asks for and the script does not write stops every compose command; one
    the script writes and compose never passes on (the hub's notebook share) leaves its reader without it."""
    asked = set(re.findall(r"\$\{([A-Z0-9_]+):\?run scripts/host-memory\.py\}", COMPOSE_TEXT))
    written = set(host_memory.budget(SHARES, LARGE_HOST_MIB, LARGE_SWAP_MIB, tier))
    assert asked == written, f"asked, not written: {sorted(asked - written)}; written, not asked: {sorted(written - asked)}"


@pytest.mark.parametrize("tier", sorted(SHARES["tiers"]))
def test_no_service_runs_without_a_limit(tier):
    """Docker reads a limit of 0 as none, and refuses one under 6 MiB."""
    for name, (mem, both) in host_memory.platform_limits(SHARES, tier, 0).items():
        assert 6 <= mem <= both, f"{name}: {mem}m memory, {both}m with swap"
    for name, (mem, both) in host_memory.platform_limits(SHARES, tier).items():
        assert 6 <= mem <= both, f"{name}: {mem}m memory, {both}m with swap"


def _python_probe(service: dict) -> bool:
    """Whether the service's healthcheck runs Python: compose's, else its image's HEALTHCHECK."""
    hc = service.get("healthcheck")
    if hc is not None:
        test = hc.get("test", [])
        return "python" in " ".join(test if isinstance(test, list) else [test])
    build = service.get("build")
    if not isinstance(build, dict):
        return False
    dockerfile = ROOT / build.get("context", ".") / build.get("dockerfile", "Dockerfile")
    m = re.search(r"^HEALTHCHECK(?:[^\n]*\\\n)*[^\n]*", dockerfile.read_text(), flags=re.M)
    return bool(m and "python" in m.group(0))


def test_every_python_probe_is_in_the_budget():
    """Both sides: a Python healthcheck the budget does not count stops its container at the probe; one it
    counts that no longer exists holds memory for nothing."""
    probed = {n for n in _long_running() if _python_probe(COMPOSE[n])}
    counted = {n for n, s in SHARES["services"].items() if s.get("probe") == "python"}
    assert probed == counted, f"probed, not counted: {sorted(probed - counted)}; counted, not probed: {sorted(counted - probed)}"


def test_the_hub_is_given_the_notebook_share():
    env = COMPOSE["notebook-hub"]["environment"]
    assert env.get("NOTEBOOK_MEMORY_SHARE_MIB") == "${NOTEBOOK_MEMORY_SHARE_MIB:?run scripts/host-memory.py}"


def test_a_larger_host_gives_the_notebooks_what_it_adds():
    """The platform's limits follow the tier, not the host."""
    small = host_memory.budget(SHARES, LARGE_HOST_MIB, LARGE_SWAP_MIB, SHARES["tier"])
    large = host_memory.budget(SHARES, 2 * LARGE_HOST_MIB, LARGE_SWAP_MIB, SHARES["tier"])
    limits = {k: v for k, v in small.items() if k.endswith("_MEM_LIMIT")}
    assert limits == {k: v for k, v in large.items() if k.endswith("_MEM_LIMIT")}, "limits grew with the host"
    added = int(large["NOTEBOOK_MEMORY_SHARE_MIB"]) - int(small["NOTEBOOK_MEMORY_SHARE_MIB"])
    assert added == LARGE_HOST_MIB


def test_a_host_the_platform_does_not_fit_is_refused():
    tier = SHARES["tiers"][SHARES["tier"]]
    with pytest.raises(ValueError, match="keeps .* in memory"):
        host_memory.budget(SHARES, int(tier["resident_mib"]) // 2, LARGE_SWAP_MIB, SHARES["tier"])


def test_without_swap_the_cold_pages_sit_in_memory():
    """A host without swap holds the whole need in memory; with half the swap, half the cold part."""
    t = SHARES["tier"]
    full = host_memory.platform_limits(SHARES, t, LARGE_SWAP_MIB)
    none = host_memory.platform_limits(SHARES, t, 0)
    for name in full:
        assert none[name][0] == none[name][1] >= full[name][1] - 1, name
        assert none[name][1] == full[name][1], name
    cold = sum(b - m for m, b in full.values())
    half = host_memory.platform_limits(SHARES, t, cold // 2)
    assert abs(sum(b - m for m, b in half.values()) - cold // 2) < len(full) + 1


def test_below_its_minimum_the_notebook_share_is_none():
    limits = sum(mem for mem, _ in host_memory.platform_limits(SHARES, SHARES["tier"]).values())
    # A host the platform fits, with less than the minimum left for notebooks.
    host = limits + host_memory.reserve_mib(SHARES) + int(SHARES["notebook_min_mib"]) // 2
    assert host_memory.budget(SHARES, host, LARGE_SWAP_MIB, SHARES["tier"])["NOTEBOOK_MEMORY_SHARE_MIB"] == "0"


def test_the_environment_template_sets_no_budget_variable():
    written = set(host_memory.budget(SHARES, LARGE_HOST_MIB, LARGE_SWAP_MIB, SHARES["tier"]))
    set_there = {line.split("=", 1)[0] for line in ENV_EXAMPLE.splitlines() if re.match(r"^[A-Z0-9_]+=", line)}
    assert not written & set_there, f".env.example sets budget variables: {sorted(written & set_there)}"


def test_the_least_notebook_share_holds_one_authoring_session():
    """notebook_min_mib is one authoring session (480 MiB, ADR-0018 dec 6) times the headroom, as the
    authoring profile's fraction of the share, which the hub holds (PROFILE_SHARE)."""
    hub = (ROOT / "services" / "notebook_hub" / "jupyterhub_config.py").read_text()
    authoring = float(re.search(r"ident\.PROFILE_AUTHORING:\s*([0-9.]+),\s*\n\}", hub).group(1))
    assert int(SHARES["notebook_min_mib"]) >= 480 * float(SHARES["headroom"]) / authoring
