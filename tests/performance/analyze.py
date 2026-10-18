# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Summarize a sample_host.sh log between two epoch seconds (docs/operations/capacity.md).

Usage: analyze.py <sample.log> <load_start_epoch> <load_end_epoch>
Prints host CPU, memory and swap over the window, swap at its start and end, and the containers that
used the most CPU (in cores) with their peak memory.
"""
import sys
from collections import defaultdict

log, t_start, t_end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
hosts, containers = [], defaultdict(list)
for line in open(log):
    p = line.split()
    if p[0] == "T":
        kv = dict(x.split("=") for x in p[2:])
        hosts.append((int(p[1]), int(kv["avail_mb"]), int(kv["swap_used_mb"]),
                      int(kv["cpu_busy"]), int(kv["cpu_total"])))
    elif p[0] == "C":
        containers[p[2]].append((int(p[1]), int(p[3]), int(p[4]) / 2**20))

window = [h for h in hosts if t_start <= h[0] <= t_end]
busy = (window[-1][3] - window[0][3]) / (window[-1][4] - window[0][4]) * 100
print(f"host: cpu {busy:.0f}%, min available {min(h[1] for h in window)} MB, "
      f"swap {window[0][2]} -> {window[-1][2]} MB (max {max(h[2] for h in window)} MB)")
rows = []
for name, samples in containers.items():
    s = [x for x in samples if t_start <= x[0] <= t_end]
    if len(s) >= 2:
        rows.append(((s[-1][1] - s[0][1]) / 1e6 / (s[-1][0] - s[0][0]), name, max(x[2] for x in s)))
print("containers by CPU (cores), peak memory:")
for cores, name, mem in sorted(rows, reverse=True)[:10]:
    print(f"  {name:45s} {cores:5.2f}  {mem:7.0f} MB")
