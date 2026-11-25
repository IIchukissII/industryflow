#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The single host's memory budget (ADR-0009 dec 6, ADR-0018 dec 6): every long-running Compose service's
memory and swap limits and heaps, from the platform's measured need at its ingest tier, and the notebook
share the platform leaves of the host.

    scripts/host-memory.py [--env .env] [--tier T] [--mem-total-mb N] [--swap-total-mb N] [--print]

A service's memory follows the software and the load, not the host, so deploy/host-memory.toml holds the
platform's measured need per ingest tier in two parts, and each service's weight in each: resident, the
pages it keeps in use, which must sit in memory; and cold, the pages it holds but does not touch, which
may sit in swap. A service's memory limit is its resident part times the headroom, and its memory-and-swap
limit adds its cold part. The host's memory holds every resident part, the reserve and the notebook
share; its swap holds the cold parts, and what of them the swap cannot hold sits in memory too.

The host is the memory the Docker engine has for its containers (`docker info`: the host's on Linux, the
virtual machine's under Docker Desktop) and the swap of /proc/meminfo, or --mem-total-mb and
--swap-total-mb for planning. The tier is --tier, else MEMORY_TIER in the env file, else the file's
default. The values are written into a block of the env file between two marker lines, replacing the
block written before; the rest of the file is the operator's and is kept. Compose reads them; a service
whose variable is missing does not start. scripts/box-deploy.sh runs this on every deploy.

Refuses (exit 2), writing nothing, when the platform at the tier and the reserve do not fit the host's
memory. A notebook share below its minimum (one authoring
session) is written as 0: the hub then refuses every start, naming why.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHARES = ROOT / "deploy" / "host-memory.toml"
BEGIN = "# >>> host memory budget (scripts/host-memory.py; do not edit) >>>"
END = "# <<< host memory budget <<<"
MIB = 1024 * 1024


def mem_total_mib() -> int:
    """The memory the Docker engine has for its containers, in MiB."""
    try:
        out = subprocess.run(["docker", "info", "--format", "{{.MemTotal}}"], capture_output=True, text=True,
                             timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        raise SystemExit(f"host-memory: cannot ask Docker for its memory ({e}); pass --mem-total-mb") from None
    if not out.isdigit() or int(out) <= 0:
        raise SystemExit(f"host-memory: Docker reports no memory total ({out!r}); pass --mem-total-mb")
    return int(out) // MIB


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


def platform_limits(shares: dict, tier: str, swap_mib: int | None = None) -> dict:
    """Each service's ``(memory, memory and swap)`` limits in MiB at ``tier``: its weights of the tier's
    measured resident and cold need, times the headroom. With ``swap_mib`` smaller than the cold part, the
    cold pages that do not fit the swap sit in memory: each service's memory limit takes the same fraction
    of its cold part. ``ValueError`` for a tier the file does not hold."""
    tiers = shares["tiers"]
    if tier not in tiers:
        raise ValueError(f"no tier {tier!r}; measured tiers: {', '.join(tiers)}")
    resident, cold = float(tiers[tier]["resident_mib"]), float(tiers[tier]["cold_mib"])
    headroom = float(shares["headroom"])
    swapped = 1.0 if swap_mib is None or cold == 0 else min(1.0, swap_mib / (cold * headroom))
    # A limit of 0 is no limit to Docker: every service keeps at least the floor.
    floor = int(shares["floor_mib"])
    probes = {"python": int(shares["probe"]["python_mib"])}
    out = {}
    for name, s in shares["services"].items():
        part = cold * float(s["cold"]) * headroom
        # A healthcheck's interpreter runs beside the service, whatever its size (``probe``).
        probe = probes[s["probe"]] if "probe" in s else 0
        mem = max(int(resident * float(s["resident"]) * headroom + part * (1 - swapped)) + probe, floor)
        out[name] = (mem, max(mem, int(resident * float(s["resident"]) * headroom + part) + probe))
    return out


def reserve_mib(shares: dict) -> int:
    """What runs beside the containers, measured, times the headroom."""
    r = shares["reserve"]
    return int((int(r["host_mib"]) + int(r["one_shot_mib"])) * float(shares["headroom"]))


def budget(shares: dict, host_mib: int, swap_mib: int, tier: str) -> dict:
    """Every variable of the budget, by name. ``ValueError`` when the platform does not fit the host."""
    limits = platform_limits(shares, tier, swap_mib)
    reserve = reserve_mib(shares)
    resident = sum(mem for mem, _ in limits.values())
    left = host_mib - resident - reserve
    if left < 0:
        raise ValueError(f"the platform at {tier} keeps {resident} MiB in memory (with {swap_mib} MiB of swap) and "
                         f"the reserve is {reserve} MiB; this host has {host_mib} MiB. Choose a lower tier, more "
                         f"swap, or a host of at least {resident + reserve} MiB")
    out = {}
    for name, (mem, both) in limits.items():
        out[limit_var(name)] = f"{mem}m"
        out[swap_limit_var(name)] = f"{both}m"
    for name, s in shares["services"].items():
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
    for var, setting in shares.get("postgres", {}).items():
        of = limits["timescaledb"][0] if setting["of"] == "limit" else reserve
        out[var] = f"{int(of * float(setting['fraction']))}MB"
    notebook = left if left >= int(shares["notebook_min_mib"]) else 0
    out["NOTEBOOK_MEMORY_SHARE_MIB"] = str(notebook)
    return out


def env_tier(env: pathlib.Path) -> str | None:
    """MEMORY_TIER in the operator's part of the env file."""
    if not env.exists():
        return None
    m = re.search(r"^MEMORY_TIER=(.+)$", env.read_text(), flags=re.M)
    return m.group(1).strip().strip("\"'") if m else None


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
    p.add_argument("--tier", help="ingest tier; default MEMORY_TIER in the env file, else the file's tier")
    p.add_argument("--mem-total-mb", type=int)
    p.add_argument("--swap-total-mb", type=int)
    p.add_argument("--print", action="store_true", help="print the budget, write nothing")
    a = p.parse_args()
    shares = tomllib.loads(SHARES.read_text())
    env = pathlib.Path(a.env)
    tier = a.tier or env_tier(env) or shares["tier"]
    host = a.mem_total_mb or mem_total_mib()
    swap = a.swap_total_mb if a.swap_total_mb is not None else swap_total_mib()
    try:
        values = budget(shares, host, swap, tier)
        if a.print:
            print("\n".join(f"{k}={v}" for k, v in values.items()))
        else:
            write_block(env, values)
            print(f"host-memory: {host} MiB at {tier}; notebook share {values['NOTEBOOK_MEMORY_SHARE_MIB']} MiB; "
                  f"{len(values)} variables in {env}")
        if values["NOTEBOOK_MEMORY_SHARE_MIB"] == "0":
            print(f"host-memory: the platform at {tier} leaves less than {shares['notebook_min_mib']} MiB of this "
                  "host for notebooks; the hub refuses every start", file=sys.stderr)
    except ValueError as e:
        print(f"host-memory: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
