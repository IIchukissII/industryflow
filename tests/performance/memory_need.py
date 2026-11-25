#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tier's measured memory need (docs/operations/capacity.md#memory, ADR-0018 dec 6), from a log of
sample_memory.sh, as the lines of deploy/host-memory.toml.

    memory_need.py LOG --tier 500/s --readings-per-second 500 --host-mib 7765 WINDOW [WINDOW ...]

WINDOW is START:END in the log's epoch seconds: the rest, the load and the journeys that make up the tier.
Per service of the budget, over every window: its resident need, the peak of anon + shmem + kernel + the
mapped files beyond shmem (the pages it keeps in use, its code included), and its cold need, the peak of
resident plus swap less the resident peak (the pages it holds untouched). Containers outside the budget
are left out. Prints the tier's totals and each service's weights in both.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import pathlib
import tomllib

SHARES = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "host-memory.toml"


def need(lines, windows, services):
    resident, total = collections.defaultdict(int), collections.defaultdict(int)
    for line in lines:
        ts, name, anon, shmem, kernel, mapped, swap = line.split()
        if name not in services or not any(a <= int(ts) <= b for a, b in windows):
            continue
        res = int(anon) + int(shmem) + int(kernel) + max(0, int(mapped) - int(shmem))
        resident[name] = max(resident[name], res)
        total[name] = max(total[name], res + int(swap))
    return {n: (resident[n], total[n] - resident[n]) for n in services}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("log")
    p.add_argument("windows", nargs="+")
    p.add_argument("--tier", required=True)
    p.add_argument("--readings-per-second", type=int, required=True)
    p.add_argument("--host-mib", type=int, required=True, help="memory of the host measured on")
    a = p.parse_args()
    services = set(tomllib.loads(SHARES.read_text())["services"])
    windows = [tuple(map(int, w.split(":"))) for w in a.windows]
    per = need(pathlib.Path(a.log).read_text().splitlines(), windows, services)
    res, cold = sum(r for r, _ in per.values()), sum(c for _, c in per.values())
    print(f'[tiers."{a.tier}"]\nresident_mib = {res}\ncold_mib = {cold}\n'
          f"readings_per_second = {a.readings_per_second}\nreference_host_mib = {a.host_mib}\n"
          f"measured = {datetime.date.today().isoformat()}\n")
    for name in sorted(per):
        r, c = per[name]
        print(f"# {name}: resident {r} MiB, cold {c} MiB\nresident = {r / res:.4f}\ncold = {c / cold if cold else 0:.4f}")


if __name__ == "__main__":
    main()
