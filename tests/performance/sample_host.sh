#!/bin/bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Runs ON the Docker host during a load test (docs/operations/capacity.md). Every 5 s: host memory,
# swap and CPU counters, and each container's cumulative CPU and current memory, read straight from
# cgroup v2 files. Never `docker stats`: polling it over every container loads the daemon enough to
# starve the ingest edge on a small host. Needs cgroup v2 with Docker's systemd cgroup driver
# (/sys/fs/cgroup/system.slice/docker-<id>.scope); containers started after the sampler are not sampled.
# Usage: sample_host.sh <out_file>   (stop with kill; then analyze.py)
set -u
out=$1
declare -A name
for id in $(docker ps -q --no-trunc); do name[$id]=$(docker inspect -f '{{.Name}}' "$id" | tr -d /); done
while true; do
  ts=$(date +%s)
  mem=$(awk '/MemAvailable/{a=$2}/SwapFree/{s=$2}/SwapTotal/{t=$2}END{printf "avail_mb=%d swap_used_mb=%d", a/1024, (t-s)/1024}' /proc/meminfo)
  read -r _ u n s i w q sq _ < /proc/stat
  echo "T $ts $mem cpu_busy=$((u + n + s + q + sq)) cpu_total=$((u + n + s + i + w + q + sq))" >> "$out"
  for id in "${!name[@]}"; do
    d=/sys/fs/cgroup/system.slice/docker-$id.scope
    [ -r "$d/cpu.stat" ] || continue
    echo "C $ts ${name[$id]} $(awk '/usage_usec/{print $2}' "$d/cpu.stat") $(cat "$d/memory.current")" >> "$out"
  done
  sleep 5
done
