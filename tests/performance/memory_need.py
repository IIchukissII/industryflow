#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The platform's measured memory need (docs/operations/capacity.md#memory, ADR-0050 dec 1-3), from a log of
sample_memory.sh, as the lines of deploy/host-memory.toml.

    memory_need.py LOG --readings-per-second 500 --host-mib 7765 [--start WINDOW] WINDOW [...]

WINDOW is START:END in the log's epoch seconds: the rest, the load at the reference rate and the journeys;
--start, every service starting (sampled each second). Per service of the budget: its resident need, the
peak over the windows of anon + shmem + kernel + the mapped files beyond shmem (the pages it keeps in use,
its code included); and its cold need, the peak of resident plus swap over the windows and the start, less
the resident need (the pages it holds untouched). A start touches every page once and is over in seconds:
its peak counts toward what a service may hold, in memory and swap, not toward what it keeps in memory.
Containers outside the budget are left out. Prints the reference and each service's two parts.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import pathlib
import tomllib

NEED = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "host-memory.toml"


def need(lines, windows, services, start=None):
    resident, total = collections.defaultdict(int), collections.defaultdict(int)
    for line in lines:
        ts, name, anon, shmem, kernel, mapped, swap = line.split()
        if name not in services:
            continue
        steady = any(a <= int(ts) <= b for a, b in windows)
        starting = start is not None and start[0] <= int(ts) <= start[1]
        if not (steady or starting):
            continue
        res = int(anon) + int(shmem) + int(kernel) + max(0, int(mapped) - int(shmem))
        if steady:
            resident[name] = max(resident[name], res)
        total[name] = max(total[name], res + int(swap))
    return {n: (resident[n], max(0, total[n] - resident[n])) for n in services}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("log")
    p.add_argument("windows", nargs="+")
    p.add_argument("--readings-per-second", type=int, required=True)
    p.add_argument("--host-mib", type=int, required=True, help="memory of the host measured on")
    p.add_argument("--start", help="START:END of every service starting")
    a = p.parse_args()
    services = set(tomllib.loads(NEED.read_text())["services"])
    windows = [tuple(map(int, w.split(":"))) for w in a.windows]
    start = tuple(map(int, a.start.split(":"))) if a.start else None
    per = need(pathlib.Path(a.log).read_text().splitlines(), windows, services, start)
    print(f"[reference]\nreadings_per_second = {a.readings_per_second}\nhost_mib = {a.host_mib}\n"
          f"measured = {datetime.date.today().isoformat()}\n")
    for name in sorted(per):
        r, c = per[name]
        print(f"[services.{name}]\nresident_mib = {r}\ncold_mib = {c}\n")


if __name__ == "__main__":
    main()
