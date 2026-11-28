# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The resource budget of a single host (ADR-0050, ADR-0018 dec 6): every long-running Compose service
holds its measured need, the notebook pool and its swap are what the platform leaves of the host, and a
notebook session has no size: the pool guarantees it its profile's measured resident need.

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
NEED = tomllib.loads((ROOT / "deploy" / "host-memory.toml").read_text())
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
# A host large enough that the whole budget is written.
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
    assert set(NEED["services"]) == _long_running()


def test_the_reserve_is_a_dated_measurement():
    assert isinstance(NEED["reserve"]["measured"], datetime.date)
    assert int(NEED["reserve"]["host_mib"]) > 0 and int(NEED["reserve"]["one_shot_mib"]) >= 0


def test_every_need_is_a_measurement_and_no_size_is_chosen():
    """ADR-0050 dec 1: each service's and each session's need is measured, at a dated reference; nothing in
    the file is an amount the host is given (a tier, a size, a share)."""
    ref = NEED["reference"]
    assert int(ref["readings_per_second"]) > 0 and int(ref["host_mib"]) > 0
    assert isinstance(ref["measured"], datetime.date)
    for name, s in NEED["services"].items():
        assert set(s) <= {"resident_mib", "cold_mib", "probe", "heap"}, name
        assert int(s["resident_mib"]) >= 0 and int(s["cold_mib"]) >= 0, name
    for name, s in NEED["sessions"].items():
        assert set(s) == {"resident_mib", "cold_mib", "measured"}, name
        assert int(s["resident_mib"]) > 0 and isinstance(s["measured"], datetime.date), name
    assert not {"tier", "tiers"} & set(NEED)


def test_the_need_was_held_by_the_host_it_was_measured_on():
    """A measurement is of a host that ran it: the services' resident need fits that host's memory."""
    resident = sum(int(s["resident_mib"]) for s in NEED["services"].values())
    assert resident < int(NEED["reference"]["host_mib"])


def test_compose_asks_for_exactly_the_variables_the_script_writes():
    """Both sides: a variable compose asks for and the script does not write stops every compose command; one
    the script writes and compose never passes on (the hub's pool) leaves its reader without it."""
    asked = set(re.findall(r"\$\{([A-Z0-9_]+):\?run scripts/host-memory\.py\}", COMPOSE_TEXT))
    written = set(host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB))
    assert asked == written, f"asked, not written: {sorted(asked - written)}; written, not asked: {sorted(written - asked)}"


def test_no_service_runs_without_a_limit():
    """Docker reads a limit of 0 as none, and refuses one under 6 MiB."""
    for swap in (0, None):
        for name, (mem, both) in host_memory.platform_limits(NEED, swap).items():
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
    counted = {n for n, s in NEED["services"].items() if s.get("probe") == "python"}
    assert probed == counted, f"probed, not counted: {sorted(probed - counted)}; counted, not probed: {sorted(counted - probed)}"


def test_the_hub_and_the_pool_are_given_the_pool_and_each_guarantee():
    """Both sides: every notebook value the script writes reaches the hub, and the hub reads each; the pool's
    cgroup gets its limits from the same values before the hub starts."""
    env = COMPOSE["notebook-hub"]["environment"]
    hub = (ROOT / "services" / "notebook_hub" / "jupyterhub_config.py").read_text()
    written = [k for k in host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB)
               if k.startswith("NOTEBOOK_") and not k.endswith(("_MEM_LIMIT", "_MEMSWAP_LIMIT"))]
    assert len(written) == 3 + len(NEED["sessions"])
    for name in written:
        assert env.get(name) == "${%s:?run scripts/host-memory.py}" % name, name
    assert '_mib_env("NOTEBOOK_MEMORY_SHARE_MIB")' in hub and '_mib_env("NOTEBOOK_SWAP_SHARE_MIB")' in hub
    assert 'f"NOTEBOOK_{p.upper()}_GUARANTEE_MIB"' in hub and '"NOTEBOOK_CGROUP_PARENT"' in hub
    pool = COMPOSE["notebook-pool"]
    assert pool["environment"] == {"POOL": "${NOTEBOOK_CGROUP_PARENT:?run scripts/host-memory.py}",
                                   "POOL_MIB": "${NOTEBOOK_MEMORY_SHARE_MIB:?run scripts/host-memory.py}",
                                   "POOL_SWAP_MIB": "${NOTEBOOK_SWAP_SHARE_MIB:?run scripts/host-memory.py}"}
    assert COMPOSE["notebook-hub"]["depends_on"]["notebook-pool"] == {"condition": "service_completed_successfully"}


def test_every_notebook_profile_has_a_measured_session():
    """ADR-0050 dec 5: a session's need is measured per profile; the hub's profiles and the budget's
    sessions are the same set."""
    hub = (ROOT / "services" / "notebook_hub" / "jupyterhub_config.py").read_text()
    resources = re.search(r"PROFILE_RESOURCES = \{(.*?)\n\}", hub, flags=re.S).group(1)
    profiles = {m.lower() for m in re.findall(r"ident\.PROFILE_([A-Z]+):", resources)}
    assert profiles == set(NEED["sessions"])


def test_a_larger_host_gives_the_pool_what_it_adds():
    """ADR-0050 dec 4: the platform's limits follow its measured need, not the host; a larger host gives the
    pool every MiB it adds, and the guarantees stay the measured ones."""
    small = host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB)
    large = host_memory.budget(NEED, 2 * LARGE_HOST_MIB, LARGE_SWAP_MIB)
    fixed = [k for k in small if k.endswith(("_MEM_LIMIT", "_GUARANTEE_MIB"))]
    assert {k: small[k] for k in fixed} == {k: large[k] for k in fixed}, "limits grew with the host"
    added = int(large["NOTEBOOK_MEMORY_SHARE_MIB"]) - int(small["NOTEBOOK_MEMORY_SHARE_MIB"])
    assert added == LARGE_HOST_MIB


def test_a_host_without_room_for_one_session_is_refused_naming_what_it_lacks():
    """ADR-0050 dec 4: the platform, the reserve and one authoring session's guarantee, or nothing starts."""
    limits = sum(mem for mem, _ in host_memory.platform_limits(NEED, LARGE_SWAP_MIB).values())
    base = limits + host_memory.reserve_mib(NEED)
    least = host_memory.guarantees(NEED)["authoring"]
    with pytest.raises(ValueError, match="it lacks 1 MiB of memory"):
        host_memory.budget(NEED, base + least - 1, LARGE_SWAP_MIB)
    assert host_memory.budget(NEED, base + least, LARGE_SWAP_MIB)["NOTEBOOK_MEMORY_SHARE_MIB"] == str(least)


def test_without_swap_the_cold_pages_sit_in_memory():
    """A host without swap holds the whole need in memory; with half the swap, half the cold part."""
    full = host_memory.platform_limits(NEED, LARGE_SWAP_MIB)
    none = host_memory.platform_limits(NEED, 0)
    for name in full:
        assert none[name][0] == none[name][1] >= full[name][1] - 1, name
        assert none[name][1] == full[name][1], name
    cold = sum(b - m for m, b in full.values())
    half = host_memory.platform_limits(NEED, cold // 2)
    assert abs(sum(b - m for m, b in half.values()) - cold // 2) < len(full) + 1


def test_a_sessions_guarantee_is_its_measured_resident_need():
    """ADR-0050 dec 5: the resident part times the headroom, at least the margin over it; no size."""
    headroom, margin = float(NEED["headroom"]), int(NEED["margin_mib"])
    out = host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB)
    for name, s in NEED["sessions"].items():
        res = int(s["resident_mib"])
        assert out[host_memory.session_var(name)] == str(int(max(res * headroom, res + margin))), name


def test_the_pools_swap_is_what_the_platforms_cold_parts_leave():
    held = sum(b - m for m, b in host_memory.platform_limits(NEED, LARGE_SWAP_MIB).values())
    out = host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB)
    assert int(out["NOTEBOOK_SWAP_SHARE_MIB"]) == LARGE_SWAP_MIB - held
    assert host_memory.budget(NEED, LARGE_HOST_MIB, 0)["NOTEBOOK_SWAP_SHARE_MIB"] == "0"


def test_the_pools_cgroup_follows_dockers_driver():
    """Under systemd a slice, which systemd nests by its name; under cgroupfs a path of its own."""
    assert host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB, "systemd")["NOTEBOOK_CGROUP_PARENT"].endswith(".slice")
    assert host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB, "cgroupfs")["NOTEBOOK_CGROUP_PARENT"].startswith("/")


def test_the_environment_template_sets_no_budget_variable():
    written = set(host_memory.budget(NEED, LARGE_HOST_MIB, LARGE_SWAP_MIB))
    set_there = {line.split("=", 1)[0] for line in ENV_EXAMPLE.splitlines() if re.match(r"^[A-Z0-9_]+=", line)}
    assert not written & set_there, f".env.example sets budget variables: {sorted(written & set_there)}"
