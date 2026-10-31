#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Deploys a commit on the long-lived environment by upgrade (ADR-0026 dec 8), from its own checkout:
#
#   scripts/box-deploy.sh [ref]          default origin/main
#
# Backup first, then the checkout moved to the ref, every image built from it, the schema sequence
# applied, and the stack recreated where it changed. On success it writes .box-deployed: the commit
# now running and the one it was upgraded from, which scripts/box-e2e.sh and scripts/box-validate.sh
# read. Refuses a checkout with local changes: what runs must be the commit it names.
#
# A full build needs room: one that fills the disk crash-loops the broker. Below BOX_MIN_FREE_GB
# (default 20) free on Docker's disk it prunes all build cache and dangling images, and refuses if
# that is still not enough, before the backup or the checkout moves.
#
# The infrastructure agent (profile ops-agent) is built and run when the environment enables it, i.e.
# `.env` sets OPS_AGENT_WEBHOOK_SECRET. The monitoring stack is recreated last: its config files are
# single-file mounts, which keep the file the checkout replaced until the container is recreated.
set -euo pipefail
cd "$(dirname "$0")/.."
ref="${1:-origin/main}"
PROFILES=(--profile notebooks --profile tenant-delete --profile backup --profile cold-export)
UP_PROFILES=(--profile notebooks)
if grep -qE '^OPS_AGENT_WEBHOOK_SECRET=.+' .env 2>/dev/null; then
    PROFILES+=(--profile ops-agent)
    UP_PROFILES+=(--profile ops-agent)
fi
min_free="${BOX_MIN_FREE_GB:-20}"

free_gb() { df -BG --output=avail "$(docker info -f '{{.DockerRootDir}}')" | tail -1 | tr -dc '0-9'; }

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "box-deploy: the checkout has local changes; refusing" >&2; exit 1
fi
from="$(sed -n 's/^deployed=//p' .box-deployed 2>/dev/null || true)"

echo "== disk"
docker builder prune -f >/dev/null
if [ "$(free_gb)" -lt "$min_free" ]; then
    echo "box-deploy: $(free_gb) GB free, below ${min_free} GB; pruning all build cache and dangling images"
    docker builder prune -af >/dev/null
    docker image prune -f >/dev/null
fi
if [ "$(free_gb)" -lt "$min_free" ]; then
    echo "box-deploy: $(free_gb) GB free after pruning, below ${min_free} GB; refusing" >&2; exit 1
fi
echo "$(free_gb) GB free"

echo "== backup"
docker compose --profile backup run --rm db-backup
echo "== checkout $ref"
git fetch -q origin
git checkout -q --detach "$ref"
sha="$(git rev-parse HEAD)"
git log --oneline -1
echo "== build"
# The stream engines stop for the build's memory on a small host, and start again below.
docker compose stop spark-master spark-worker spark-streaming spark-aggregations
docker compose "${PROFILES[@]}" build
echo "== schema"
docker compose up --no-deps --exit-code-from db-migrate db-migrate
echo "== up"
docker compose "${UP_PROFILES[@]}" up -d
# The edges cache their upstreams' addresses: the device edge is recreated, the web edge reloaded.
docker compose up -d --no-deps --force-recreate ingestion-edge
docker compose exec -T frontend nginx -s reload
docker compose start spark-master spark-worker spark-streaming spark-aggregations
echo "== monitoring config"
docker compose up -d --no-deps --force-recreate prometheus alertmanager grafana
printf 'deployed=%s\nfrom=%s\nat=%s\n' "$sha" "${from:-}" "$(date -u +%FT%TZ)" > .box-deployed
echo "box-deploy $sha"
