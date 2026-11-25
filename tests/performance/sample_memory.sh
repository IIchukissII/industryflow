#!/bin/bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Runs ON the Docker host while a tier is measured (docs/operations/capacity.md#memory, ADR-0018 dec 6).
# Every 10 s, each running container's memory from cgroup v2, in MiB:
#   <epoch> <name> <anon> <shmem> <kernel> <file_mapped> <swap>
# Not memory.current: it holds the page cache, which the kernel reclaims. A container that restarts or goes
# away between listing and reading is skipped for that round. Needs cgroup v2 with Docker's systemd cgroup
# driver (/sys/fs/cgroup/system.slice/docker-<id>.scope). Run it under loose limits, so what is measured is
# the need and not a limit.
# Usage: sample_memory.sh <out_file>   (stop with kill; then memory_need.py)
out=$1
while true; do
  ts=$(date +%s)
  for id in $(docker ps -q --no-trunc 2>/dev/null); do
    d=/sys/fs/cgroup/system.slice/docker-$id.scope
    stat=$(cat "$d/memory.stat" 2>/dev/null) || continue
    swap=$(cat "$d/memory.swap.current" 2>/dev/null) || continue
    name=$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's|^/||; s|^industryflow-||') || continue
    [ -n "$name" ] || continue
    awk -v t="$ts" -v n="$name" -v sw="$swap" '
      $1 == "anon" { a = $2 } $1 == "shmem" { s = $2 } $1 == "kernel" { k = $2 } $1 == "file_mapped" { f = $2 }
      END { printf "%s %s %d %d %d %d %d\n", t, n, a / 1048576, s / 1048576, k / 1048576, f / 1048576, sw / 1048576 }' <<<"$stat"
  done >> "$out"
  sleep 10
done
