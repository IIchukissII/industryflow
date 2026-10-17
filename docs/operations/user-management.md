<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# User management

Roles, tenant scoping, and the API and CLI for managing users and companies (tenants).
Authentication: [authentication.md](authentication.md). Scoping:
[ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md) dec 5; tenant
lifecycle: [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md).

## Roles

| Role | Scope |
|------|-------|
| `observer` | Read-only access to the tenant's data |
| `engineer` | Manage equipment, sensors, alert rules and models in the tenant; acknowledge and label alerts |
| `admin` | `engineer`, plus administration of their own company |
| Superuser (`is_superuser=true`) | Cross-tenant and platform operations; passes any role gate |

`role` is one of `observer`, `engineer`, `admin` (database `CHECK` constraint `check_role`).
`is_superuser` is a separate flag. Every route that changes tenant data needs `engineer` or `admin`;
an `observer` gets **403**. There is no self-registration.

## Tenant scoping

| Endpoint | Per-tenant admin | Superuser |
|----------|------------------|-----------|
| `GET /api/users` | Own company's users | All users |
| `POST /api/users` | Into own company; cannot grant superuser | Any company; may grant superuser |
| `PATCH` / `DELETE /api/users/{id}` | Own company's users (404 for another company's, 403 for a superuser); cannot change `company_id` or `is_superuser` | Any |
| `DELETE /api/users/{id}` on self | 400 | 400 |
| `PATCH /users/me` (any user) | Own email and password only | Same |
| `GET /api/companies`, `GET /api/companies/{id}` | Own company only (404 otherwise) | Any |
| `PUT /api/companies/{id}` | Rename own company only (404 otherwise); cannot change `is_active` | Any |
| `POST /api/companies`, `DELETE /api/companies/{id}` | 403 | Allowed |

A request for another tenant's company or user returns **404**, so other tenants' ids are not
confirmed.

## API

```bash
curl -k -b jar.txt -H "X-CSRF-Token: $CSRF" -X POST https://<host>/api/users \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "<password>", "role": "engineer"}'
```

| Field | Rule |
|-------|------|
| `password` | At least 8 characters |
| `role` | Default `observer` |
| `company_id` | Superuser only; a tenant admin always creates into their own company |
| `is_active` | Settable by `PATCH`; re-enables a user after company reactivation |

## CLI

`scripts/manage_users.py` and `scripts/seed_users.py` connect as the gateway role
(`API_GATEWAY_DB_USER` / `API_GATEWAY_DB_PASSWORD`, `DB_HOST` / `DB_PORT` / `DB_NAME`) with
`DB_SSLMODE` / `DB_SSLROOTCERT`
([ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md)), and exit non-zero on failure.
Run them in a one-off `api-gateway` container; secrets come from the environment (`-e NAME`, e.g.
under `op run`), never from arguments.

```bash
RUN='docker compose run --rm --no-deps -v ./scripts:/scripts:ro -e PYTHONPATH=/app'
```

| Task | Command |
|------|---------|
| Create a user (password prompted twice) | `$RUN -it api-gateway python /scripts/manage_users.py create --email <email> --company-id <company-id> --role admin [--superuser]` |
| Reset a password from stdin | `printf '%s\n' "$NEW_PASSWORD" \| $RUN -T api-gateway python /scripts/manage_users.py reset-password --email <email> --password-stdin` |
| List users | `$RUN api-gateway python /scripts/manage_users.py list` |
| Delete a user | `$RUN api-gateway python /scripts/manage_users.py delete --email <email> --yes` |
| Seed users from the environment | `op run -- $RUN -e ADMIN_USER_1_EMAIL -e ADMIN_USER_1_PASSWORD -e ADMIN_USER_1_COMPANY_ID api-gateway python /scripts/seed_users.py` |

`create` also takes `--password-stdin`. A CLI password reset ends the user's sessions at their next
refresh.

| `seed_users.py` variable (`n` = 1..9) | Default | Meaning |
|---------------------------------------|---------|---------|
| `ADMIN_USER_<n>_EMAIL`, `_PASSWORD`, `_COMPANY_ID` | none (all three required) | The user |
| `ADMIN_USER_<n>_ROLE` | `admin` | `admin`, `engineer` or `observer` |
| `ADMIN_USER_<n>_SUPERUSER` | `false` | `true` grants superuser |

A CLI-created user is a superuser only with `--superuser` or `ADMIN_USER_<n>_SUPERUSER=true`; `admin`
alone is per-tenant.

## Companies

| Task | How | Effect |
|------|-----|--------|
| Provision (API) | Superuser `POST /api/companies` | Company row and `tenant_<uuid>` schema in one transaction ([ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)) via the SECURITY DEFINER `provision_company()` |
| Provision (operator) | `scripts/create-tenant.sh "<company-name>" [<company-id>]` | Inserts the company row and runs `create_tenant_schema()` as the database superuser (`psql` in the `timescaledb` container); prints the `company_id`. Idempotent on the name |
| Rename | `PUT /api/companies/{id}` | Through `update_company()` |
| Deactivate | Superuser `DELETE /api/companies/{id}`, or `PUT` with `"is_active": false` | Company and all its users inactive, sessions revoked; schema and data kept. A superuser cannot deactivate their own company (400) |
| Reactivate | Superuser `PUT` with `"is_active": true` | Company active; users re-enabled one by one (`PATCH /api/users/{id}` `is_active`) |
| Drop a tenant's data | Operator action on the database | No API |

Company names are 1-255 characters, no control characters, unique
([`21-company-provisioning.sql`](../../infrastructure/timescaledb/init-scripts/21-company-provisioning.sql)).
The gateway's database role cannot write the tenant registry or create schemas directly.
