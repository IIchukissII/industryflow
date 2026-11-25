#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The single host's memory budget (ADR-0009 dec 6, ADR-0018 dec 6): every long-running Compose service's
memory limit and heaps, and the notebook share, derived from the host's memory.

    scripts/host-memory.py [--env .env] [--mem-total-mb N] [--print]

Reads the shares from deploy/host-memory.toml and the memory the Docker engine has for its containers
(`docker info`: the host's on Linux, the virtual machine's under Docker Desktop; or --mem-total-mb), and writes them as variables into a block of the env file between two marker lines,
replacing the block it wrote before; the rest of the file is the operator's and is kept. Compose reads
the variables; a service whose variable is missing does not start. Run it once before the first `up`
and again after the host's memory changes; scripts/box-deploy.sh runs it on every deploy.

Refuses (exit 2) when the shares and the reserve leave no notebook share, so a budget that does not fit
the host is not written.
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


def limit_var(service: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", service.upper()) + "_MEM_LIMIT"


def budget(shares: dict, host_mib: int) -> dict:
    """Every variable of the budget, by name. ``ValueError`` when the budget does not fit the host."""
    services = shares["services"]
    reserve = float(shares["reserve"])
    used = reserve + sum(float(s["share"]) for s in services.values())
    notebook = round(1.0 - used, 6)
    if notebook < float(shares["notebook_min"]):
        raise ValueError(f"shares and reserve take {used:.0%} of the host; the notebook share would be "
                         f"{notebook:.0%}, below its minimum {float(shares['notebook_min']):.0%}")
    out = {}
    limits = {}
    for name, s in services.items():
        mib = int(host_mib * float(s["share"]))
        limits[name] = mib
        out[limit_var(name)] = f"{mib}m"
    for name, s in services.items():
        heap = s.get("heap")
        if heap is None:
            continue
        mib = max(int(limits[name] * float(heap["fraction"])), int(heap.get("min_mib", 0)))
        if heap["kind"] == "jvm-opts":
            out[heap["var"]] = f"-Xms{mib}m -Xmx{mib}m"
        elif heap["kind"] == "size":
            out[heap["var"]] = f"{mib}m"
        else:
            raise ValueError(f"{name}: unknown heap kind {heap['kind']!r}")
    for var, of in shares.get("postgres", {}).items():
        out[var] = f"{int(limits['timescaledb'] * float(of))}MB"
    out["NOTEBOOK_MEMORY_SHARE_MIB"] = str(int(host_mib * notebook))
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
    p.add_argument("--print", action="store_true", help="print the budget, write nothing")
    a = p.parse_args()
    host = a.mem_total_mb or mem_total_mib()
    try:
        values = budget(tomllib.loads(SHARES.read_text()), host)
        if a.print:
            print("\n".join(f"{k}={v}" for k, v in values.items()))
        else:
            write_block(pathlib.Path(a.env), values)
            print(f"host-memory: {host} MiB; notebook share {values['NOTEBOOK_MEMORY_SHARE_MIB']} MiB; "
                  f"{len(values)} variables in {a.env}")
    except ValueError as e:
        print(f"host-memory: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
