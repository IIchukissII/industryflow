#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Provision a tenant at runtime: create the company row and its isolated schema.
# Nothing about a tenant is hardcoded in the repo — the operator brings the name/id here.
#
# Usage:
#   scripts/create-tenant.sh "<Company Name>" [company-uuid]
#   TENANT_NAME="<Company Name>" TENANT_ID=<uuid> scripts/create-tenant.sh
#
# If no UUID is given one is generated. Prints the resulting company_id + schema; use that
# company_id for ADMIN_USER_*_COMPANY_ID (then run scripts/seed_users.py) or for the API.
set -e

NAME="${1:-$TENANT_NAME}"
ID="${2:-$TENANT_ID}"

if [ -z "$NAME" ]; then
    echo "usage: scripts/create-tenant.sh \"<Company Name>\" [company-uuid]" >&2
    exit 1
fi
# Keep the SQL simple/safe: this is an operator CLI, not a request handler.
case "$NAME$ID" in
    *"'"*) echo "error: single quotes are not supported in the name/id" >&2; exit 1 ;;
esac

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

SQL=$(cat <<SQL
DO \$do\$
DECLARE
    v_id UUID := COALESCE(NULLIF('${ID}', ''), gen_random_uuid()::text)::uuid;
BEGIN
    INSERT INTO public.companies (company_id, company_name, schema_name)
    VALUES (v_id, '${NAME}', '')
    ON CONFLICT (company_name) DO NOTHING;

    -- Resolve the id in case the company already existed (name is UNIQUE).
    SELECT company_id INTO v_id FROM public.companies WHERE company_name = '${NAME}';

    PERFORM create_tenant_schema(v_id, '${NAME}');

    RAISE NOTICE 'tenant ready  company_name=%  company_id=%  schema=%',
        '${NAME}', v_id, (SELECT schema_name FROM public.companies WHERE company_id = v_id);
END
\$do\$;
SQL
)

printf '%s\n' "$SQL" | docker compose exec -T timescaledb sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
