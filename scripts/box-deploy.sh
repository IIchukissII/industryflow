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
set -euo pipefail
cd "$(dirname "$0")/.."
ref="${1:-origin/main}"
PROFILES=(--profile notebooks --profile tenant-delete --profile backup --profile cold-export)

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "box-deploy: the checkout has local changes; refusing" >&2; exit 1
fi
from="$(sed -n 's/^deployed=//p' .box-deployed 2>/dev/null || true)"

echo "== backup"
docker compose --profile backup run --rm db-backup
echo "== checkout $ref"
git fetch -q origin
git checkout -q --detach "$ref"
sha="$(git rev-parse HEAD)"
git log --oneline -1
echo "== build"
docker builder prune -f >/dev/null
# The stream engines stop for the build's memory on a small host, and start again below.
docker compose stop spark-master spark-worker spark-streaming spark-aggregations
docker compose "${PROFILES[@]}" build
echo "== schema"
docker compose up --no-deps --exit-code-from db-migrate db-migrate
echo "== up"
docker compose --profile notebooks up -d
# The edges cache their upstreams' addresses: the device edge is recreated, the web edge reloaded.
docker compose up -d --no-deps --force-recreate ingestion-edge
docker compose exec -T frontend nginx -s reload
docker compose start spark-master spark-worker spark-streaming spark-aggregations
printf 'deployed=%s\nfrom=%s\nat=%s\n' "$sha" "${from:-}" "$(date -u +%FT%TZ)" > .box-deployed
echo "box-deploy $sha"
