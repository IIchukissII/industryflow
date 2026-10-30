#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Runs the end-to-end suite (tests/e2e) against the deployment in this checkout, on its own host
# (ADR-0026 dec 8, 10). Run from the long-lived environment after scripts/box-deploy.sh deployed it:
#
#   scripts/box-e2e.sh
#
# It builds the suite's runner image, creates a platform superuser for the run (removed on exit,
# whatever the outcome), runs every journey with skips counted as failures, and ends with one line:
#   box-e2e <commit> passed|failed
# scripts/box-validate.sh runs it over SSH and posts that result as the commit's status.
#
#   E2E_EDGE_HOST  the name the edges' certificate carries (default industryflow.local)
#   E2E_EDGE_IP    the address that name reaches (default this host's first address)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SHA="$(git rev-parse HEAD)"
# What runs must be the commit the result names: a clean checkout, deployed from this commit.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "box-e2e: the checkout has local changes; refusing" >&2; exit 2
fi
deployed="$(sed -n 's/^deployed=//p' .box-deployed 2>/dev/null || true)"
if [ "$deployed" != "$SHA" ]; then
    echo "box-e2e: the running stack was deployed from '${deployed:-nothing recorded}', not $SHA; run scripts/box-deploy.sh" >&2
    exit 2
fi
HOST="${E2E_EDGE_HOST:-industryflow.local}"
IP="${E2E_EDGE_IP:-$(hostname -I | cut -d' ' -f1)}"
WORK="$(mktemp -d /tmp/industryflow-e2e.XXXXXX)"
# Shared with the authoring image's user; sticky, so no one replaces another's file.
chmod 1777 "$WORK"
ENVFILE="$(mktemp /tmp/industryflow-e2e-env.XXXXXX)"
EMAIL="e2e-root-$(date +%s)@example.com"
RUN_GW=(docker compose run --rm --no-deps -T -v ./scripts:/scripts:ro -e PYTHONPATH=/app api-gateway)

cleanup() {
    "${RUN_GW[@]}" python /scripts/manage_users.py delete --email "$EMAIL" --yes >/dev/null 2>&1 || \
        echo "box-e2e: could not remove $EMAIL; remove it by hand" >&2
    rm -rf "$WORK" "$ENVFILE"
}
trap cleanup EXIT

# The run's superuser lives in the oldest active tenant: a superuser belongs to one, and acts on all.
HOME_TENANT="$(docker exec industryflow-timescaledb psql -U postgres -d industryflow -tAc \
    "SELECT company_id FROM public.companies WHERE is_active ORDER BY created_at LIMIT 1")"
PASSWORD="$(openssl rand -hex 24)"
printf '%s\n' "$PASSWORD" | "${RUN_GW[@]}" python /scripts/manage_users.py create --email "$EMAIL" \
    --company-id "$HOME_TENANT" --role admin --superuser --password-stdin >/dev/null

docker build -q -t industryflow-e2e tests/e2e >/dev/null
# The run's settings, the superuser's password among them, go in a file only this user reads (mktemp
# creates it 0600, outside the shared work directory): never on a command line.
cat > "$ENVFILE" <<VARS
IF_E2E_REQUIRE=1
IF_E2E_EDGE=https://$HOST
IF_E2E_INGEST=https://$HOST:8443/ingest
IF_E2E_CA=$ROOT/deploy/certs/ca.crt
IF_E2E_SUPERUSER_EMAIL=$EMAIL
IF_E2E_SUPERUSER_PASSWORD=$PASSWORD
IF_E2E_COMPOSE_DIR=$ROOT
IF_E2E_WORK=$WORK
IF_E2E_AUTHORING_IMAGE=${E2E_AUTHORING_IMAGE:-industryflow-notebook-authoring:latest}
HOME=/tmp
VARS
status=failed
if docker run --rm \
    --add-host "$HOST:$IP" \
    -u "$(id -u):$(id -g)" --group-add "$(stat -c %g /var/run/docker.sock)" \
    -v /var/run/docker.sock:/var/run/docker.sock -v "$ROOT:$ROOT" -v "$WORK:$WORK" -w "$ROOT" \
    --env-file "$ENVFILE" \
    industryflow-e2e tests/e2e -p no:cacheprovider -v --tb=short "$@"; then
    status=passed
fi
echo "box-e2e $SHA $status"
[ "$status" = passed ]
