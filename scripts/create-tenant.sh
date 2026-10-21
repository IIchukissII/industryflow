#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Provision a tenant at runtime through public.provision_company(), the one provisioning path
# (ADR-0033 dec 1), run as the API gateway's role, so the operator CLI holds no privilege the API
# lacks. The platform generates the company_id. Idempotent on the name: an existing company's id is
# printed instead.
#
# Usage:
#   scripts/create-tenant.sh "<Company Name>"
#   TENANT_NAME="<Company Name>" scripts/create-tenant.sh
#
# Prints the company_id on stdout; use it for ADMIN_USER_*_COMPANY_ID (then run
# scripts/seed_users.py), scripts/manage_users.py or the API.
set -e

NAME="${1:-$TENANT_NAME}"

if [ -z "$NAME" ] || [ $# -gt 1 ]; then
    echo "usage: scripts/create-tenant.sh \"<Company Name>\"" >&2
    exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

# The name travels as a psql variable (:'name' quotes it), never spliced into the SQL text.
SQL=$(cat <<'SQL'
SET ROLE api_gateway_user;
SELECT EXISTS (SELECT 1 FROM public.companies WHERE company_name = :'name') AS known,
       (SELECT company_id FROM public.companies WHERE company_name = :'name') AS existing_company_id \gset
\if :known
\echo :existing_company_id
\else
SELECT company_id AS new_company_id FROM public.provision_company(:'name') \gset
\echo :new_company_id
\endif
SQL
)

printf '%s\n' "$SQL" | docker compose exec -T timescaledb sh -c \
    'psql -X -q -v ON_ERROR_STOP=1 -v name="$1" -U "$POSTGRES_USER" -d "$POSTGRES_DB"' sh "$NAME"
