#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A notebook session's measured need (docs/operations/capacity.md#a-notebook-session, ADR-0050 dec 5),
from a log of sample_memory.sh taken each second, as the lines of deploy/host-memory.toml.

    session_need.py LOG --container NAME --profile authoring|analytics --reclaimed EPOCH

NAME is the session's container (its name or a prefix of it); EPOCH the second its untouched pages were
reclaimed (memory.reclaim). Its resident need is the peak of anon + shmem + kernel + the mapped files
beyond shmem after the reclaim, while the journey carries on: the pages the session uses. Its cold need is
the peak of resident plus swap over the whole session, its start and its first load of data included, less
the resident need: what it holds and does not use.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib


def need(lines, container: str, reclaimed: int) -> tuple[int, int]:
    resident = total = 0
    for line in lines:
        ts, name, anon, shmem, kernel, mapped, swap = line.split()
        if not name.startswith(container):
            continue
        res = int(anon) + int(shmem) + int(kernel) + max(0, int(mapped) - int(shmem))
        if int(ts) >= reclaimed:
            resident = max(resident, res)
        total = max(total, res + int(swap))
    if not total:
        raise SystemExit(f"no samples of {container!r}")
    return resident, max(0, total - resident)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("log")
    p.add_argument("--container", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--reclaimed", type=int, required=True)
    a = p.parse_args()
    res, cold = need(pathlib.Path(a.log).read_text().splitlines(), a.container, a.reclaimed)
    print(f"[sessions.{a.profile}]\nresident_mib = {res}\ncold_mib = {cold}\n"
          f"measured = {datetime.date.today().isoformat()}")


if __name__ == "__main__":
    main()
