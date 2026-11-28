#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The resource budget of a single host (ADR-0050; the notebook pool, ADR-0018 dec 6): every long-running
Compose service's memory and swap limits and heaps, the notebook pool and its swap, and each notebook
profile's guarantee, derived from the platform's measured need and the host's memory and swap.

    scripts/host-memory.py [--env .env] [--mem-total-mb N] [--swap-total-mb N] [--print]

A service's memory follows its software and its load, not the host, so deploy/host-memory.toml holds each
service's measured need at the reference rate, in two parts: resident, the pages it keeps in use, which
must sit in memory; and cold, the pages it holds but does not touch, which may sit in swap. A service's
memory limit is its resident part times the headroom, and its memory-and-swap limit adds its cold part.
No amount is chosen: the host's memory holds every resident part and the reserve, and what is left is the
notebook pool; its swap holds the cold parts, and what is left is the pool's swap. What of the cold parts
the swap cannot hold sits in memory.

The notebook sessions run in one cgroup whose limits are the pool and its swap (ADR-0050 dec 5): within it
a session may grow into the whole pool, and the pool guarantees each its profile's measured resident part
times the headroom. The cgroup is a systemd slice where Docker's cgroup driver is systemd, else a cgroupfs
path; notebook-pool sets its limits.

The host is the memory the Docker engine has for its containers (`docker info`: the host's on Linux, the
virtual machine's under Docker Desktop) and the swap of /proc/meminfo, or --mem-total-mb and
--swap-total-mb for planning. The values are written into a block of the env file between two marker
lines, replacing the block written before; the rest of the file is the operator's and is kept. Compose
reads them; a service whose variable is missing does not start. scripts/box-deploy.sh runs this on every
deploy.

Refuses (exit 2), writing nothing, when the platform, the reserve and one authoring session's guarantee do
not fit the host's memory, naming what the host lacks.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
NEED = ROOT / "deploy" / "host-memory.toml"
BEGIN = "# >>> host memory budget (scripts/host-memory.py; do not edit) >>>"
END = "# <<< host memory budget <<<"
MIB = 1024 * 1024
# The notebook pool's cgroup (ADR-0018 dec 6): a systemd slice under the systemd driver, which nests it under
# industryflow.slice by its name; a path of its own under cgroupfs.
POOL_SLICE = "industryflow-notebooks.slice"
POOL_PATH = "/industryflow-notebooks"


def _docker_info(field: str, flag: str) -> str:
    try:
        return subprocess.run(["docker", "info", "--format", "{{." + field + "}}"], capture_output=True, text=True,
                              timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        raise SystemExit(f"host-memory: cannot ask Docker for its {field} ({e}); pass {flag}") from None


def mem_total_mib() -> int:
    """The memory the Docker engine has for its containers, in MiB."""
    out = _docker_info("MemTotal", "--mem-total-mb")
    if not out.isdigit() or int(out) <= 0:
        raise SystemExit(f"host-memory: Docker reports no memory total ({out!r}); pass --mem-total-mb")
    return int(out) // MIB


def cgroup_driver() -> str:
    """Docker's cgroup driver, ``systemd`` or ``cgroupfs``."""
    out = _docker_info("CgroupDriver", "--cgroup-driver")
    if out not in ("systemd", "cgroupfs"):
        raise SystemExit(f"host-memory: unknown Docker cgroup driver {out!r}; pass --cgroup-driver")
    return out


def swap_total_mib() -> int:
    """The host's swap, in MiB (Linux)."""
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("SwapTotal:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    raise SystemExit("host-memory: no SwapTotal in /proc/meminfo; pass --swap-total-mb")


def limit_var(service: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", service.upper()) + "_MEM_LIMIT"


def swap_limit_var(service: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", service.upper()) + "_MEMSWAP_LIMIT"


def _held(need: dict, mib: float) -> float:
    """A resident part with its margin: the headroom, at least ``margin_mib`` over the measurement. A small
    service's bursts (a worker, a burst of connections) are an amount, not a fraction; the margin also keeps
    every limit above 0, which Docker reads as none."""
    return max(mib * float(need["headroom"]), mib + int(need["margin_mib"]))


def platform_limits(need: dict, swap_mib: int | None = None) -> dict:
    """Each service's ``(memory, memory and swap)`` limits in MiB: its measured resident and cold need,
    times the headroom. With ``swap_mib`` smaller than the cold parts, the cold pages that do not fit the swap
    sit in memory: each service's memory limit takes the same fraction of its cold part."""
    headroom = float(need["headroom"])
    cold = sum(float(s["cold_mib"]) for s in need["services"].values())
    swapped = 1.0 if swap_mib is None or cold == 0 else min(1.0, swap_mib / (cold * headroom))
    probes = {"python": int(need["probe"]["python_mib"])}
    out = {}
    for name, s in need["services"].items():
        res, part = float(s["resident_mib"]), float(s["cold_mib"])
        # A healthcheck's interpreter runs beside the service, whatever its size (``probe``).
        probe = probes[s["probe"]] if "probe" in s else 0
        mem = int(_held(need, res) + part * headroom * (1 - swapped)) + probe
        out[name] = (mem, max(mem, int(_held(need, res) + part * headroom) + probe))
    return out


def session_var(profile: str) -> str:
    return f"NOTEBOOK_{profile.upper()}_GUARANTEE_MIB"


def guarantees(need: dict) -> dict:
    """What the pool guarantees one session of each profile, in MiB (ADR-0050 dec 5): its measured resident
    part with its margin. A session has no size: it may grow into what the pool leaves."""
    return {p: int(_held(need, float(s["resident_mib"]))) for p, s in need["sessions"].items()}


def reserve_mib(need: dict) -> int:
    """What runs beside the containers, measured, times the headroom."""
    r = need["reserve"]
    return int((int(r["host_mib"]) + int(r["one_shot_mib"])) * float(need["headroom"]))


def budget(need: dict, host_mib: int, swap_mib: int, driver: str = "systemd") -> dict:
    """Every variable of the budget, by name. ``ValueError`` when the platform and one authoring session do
    not fit the host."""
    limits = platform_limits(need, swap_mib)
    reserve = reserve_mib(need)
    resident = sum(mem for mem, _ in limits.values())
    least = guarantees(need)
    pool = host_mib - resident - reserve
    if pool < least["authoring"]:
        # More swap helps only while some cold part sits in memory.
        cold = sum(both - mem for mem, both in platform_limits(need).values())
        held = min(least['authoring'] - pool, cold - swap_mib)
        swap = f" (more swap would hold up to {held} MiB of it, cold pages)" if swap_mib < cold else ""
        raise ValueError(f"the platform keeps {resident} MiB in memory (with {swap_mib} MiB of swap), the reserve is "
                         f"{reserve} MiB and one notebook session needs {least['authoring']} MiB; this host has "
                         f"{host_mib} MiB: it lacks {least['authoring'] - pool} MiB of memory{swap}")
    out = {}
    for name, (mem, both) in limits.items():
        out[limit_var(name)] = f"{mem}m"
        out[swap_limit_var(name)] = f"{both}m"
    for name, s in need["services"].items():
        heap = s.get("heap")
        if heap is None:
            continue
        mib = max(int(limits[name][0] * float(heap["fraction"])), int(heap.get("min_mib", 0)))
        if heap["kind"] == "jvm-opts":
            out[heap["var"]] = f"-Xms{mib}m -Xmx{mib}m"
        elif heap["kind"] == "size":
            out[heap["var"]] = f"{mib}m"
        else:
            raise ValueError(f"{name}: unknown heap kind {heap['kind']!r}")
    for var, setting in need.get("postgres", {}).items():
        of = limits["timescaledb"][0] if setting["of"] == "limit" else reserve
        out[var] = f"{int(of * float(setting['fraction']))}MB"
    out["NOTEBOOK_MEMORY_SHARE_MIB"] = str(pool)
    out["NOTEBOOK_SWAP_SHARE_MIB"] = str(max(0, swap_mib - sum(both - mem for mem, both in limits.values())))
    for profile, mib in least.items():
        out[session_var(profile)] = str(mib)
    out["NOTEBOOK_CGROUP_PARENT"] = POOL_SLICE if driver == "systemd" else POOL_PATH
    return out


def write_block(env: pathlib.Path, values: dict) -> None:
    text = env.read_text() if env.exists() else ""
    kept = re.sub(rf"\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n?", "\n", text, flags=re.S).rstrip("\n")
    for name in values:
        if re.search(rf"^{name}=", kept, flags=re.M):
            raise ValueError(f"{env} sets {name} outside the budget block; remove it there")
    block = "\n".join([BEGIN, *[f"{k}={v}" for k, v in values.items()], END])
    env.write_text((kept + "\n\n" if kept else "") + block + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--env", default=str(ROOT / ".env"))
    p.add_argument("--mem-total-mb", type=int)
    p.add_argument("--swap-total-mb", type=int)
    p.add_argument("--cgroup-driver", choices=("systemd", "cgroupfs"))
    p.add_argument("--print", action="store_true", help="print the budget, write nothing")
    a = p.parse_args()
    need = tomllib.loads(NEED.read_text())
    env = pathlib.Path(a.env)
    host = a.mem_total_mb or mem_total_mib()
    swap = a.swap_total_mb if a.swap_total_mb is not None else swap_total_mib()
    driver = a.cgroup_driver or cgroup_driver()
    try:
        values = budget(need, host, swap, driver)
        if a.print:
            print("\n".join(f"{k}={v}" for k, v in values.items()))
        else:
            write_block(env, values)
            print(f"host-memory: {host} MiB; notebook pool {values['NOTEBOOK_MEMORY_SHARE_MIB']} MiB, "
                  f"swap {values['NOTEBOOK_SWAP_SHARE_MIB']} MiB; {len(values)} variables in {env}")
    except ValueError as e:
        print(f"host-memory: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
