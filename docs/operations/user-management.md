<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# User management

Users belong to a **company (tenant)** and have a **role**; a small number are
**superusers** (platform operators). Authentication is covered in
**[authentication.md](authentication.md)**.

## Roles

| Role | Intent |
|------|--------|
| `observer` | Read-only access to the tenant's data |
| `engineer` | Manage equipment, sensors, alert rules, models within the tenant; acknowledge and label alerts |
| `admin` | Everything `engineer` can, plus tenant administration within their **own** company |
| *superuser* (`is_superuser=true`) | Cross-tenant / platform operations |

`role` is one of `observer | engineer | admin` (a DB CHECK constraint enforces it);
`is_superuser` is a separate flag. A superuser passes any role gate.

Every route that changes tenant data needs `engineer` or `admin`: creating, editing and deleting
equipment, sensors and alert rules, switching a rule's detection mode, and acknowledging or
labelling an alert. An `observer` gets **403** on all of them and can change nothing.

## Tenant scoping (ADR-0004 decision 5)

Authorization is scoped to the caller's company. A per-tenant **admin acts only on their
own company**; **cross-company actions require a superuser**:

| Endpoint | Per-tenant admin | Superuser |
|----------|------------------|-----------|
| `GET /api/users` | own company's users | all users |
| `POST /api/users` | creates into own company only | any company; may grant superuser |
| `PATCH /api/users/{id}`, `DELETE /api/users/{id}` | own company's non-superusers (404 otherwise); cannot change company or superuser | any |
| `PATCH /users/me` (any user) | own email and password only | same |
| `GET /api/companies`, `GET /api/companies/{id}` | own company only | any |
| `PUT /api/companies/{id}` | rename own company only (404 otherwise); cannot change `is_active` | any |
| `POST /api/companies`, `DELETE /api/companies/{id}` | forbidden | allowed (provisioning) |

Requests that target another tenant's company return **404** (so other tenants' IDs are
not confirmed). Every data query is constrained to the authenticated tenant.

## Managing users

Create or manage users with the API, or with the CLI helpers for bootstrapping and recovery. There
is no self-registration.

```bash
# API: create a user (admin: into own company; superuser: any company_id)
curl -k -b jar.txt -H "X-CSRF-Token: $CSRF" -X POST https://<host>/api/users \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "...", "role": "engineer"}'
```

The CLI helpers connect to the database as the gateway role (`API_GATEWAY_DB_USER` /
`API_GATEWAY_DB_PASSWORD`, `DB_HOST`/`DB_PORT`/`DB_NAME`) with the gateway's TLS settings: by
default `verify-full` against `DB_SSLROOTCERT` (ADR-0017). Run them with the secrets injected into
the environment (`op run -- ...`), never on the command line. They exit non-zero on any failure.

```bash
# Interactive: the password is prompted for (twice), never passed as an argument.
python3 scripts/manage_users.py create --email user@example.com --company-id <uuid> --role admin
python3 scripts/manage_users.py reset-password --email user@example.com
# Non-interactive: read the password from stdin (one line).
printf '%s\n' "$NEW_PASSWORD" | python3 scripts/manage_users.py create ... --password-stdin
python3 scripts/manage_users.py list
python3 scripts/manage_users.py delete --email user@example.com [--yes]

# Seed the first admins from ADMIN_USER_<n>_{EMAIL,PASSWORD,COMPANY_ID[,ROLE]} in the environment.
op run -- python3 scripts/seed_users.py
```

A user created by the CLI is a tenant user. It becomes a **superuser** only with an explicit
`--superuser` (`manage_users.py create`) or `ADMIN_USER_<n>_SUPERUSER=true` (`seed_users.py`):
an `admin` is a per-tenant role and never cross-tenant on its own (ADR-0004 dec 5). A password
reset by the CLI ends the user's existing sessions at their next refresh.

## Companies (tenants)

A superuser provisions a tenant with `POST /api/companies`. That creates the company and its
isolated `tenant_<uuid>` schema in one transaction (see
**[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**). The gateway's database role
cannot write the tenant registry or create schemas itself. It calls the SECURITY DEFINER functions
`provision_company()` and `update_company()`
([`20-company-provisioning.sql`](../../infrastructure/timescaledb/init-scripts/20-company-provisioning.sql)),
which validate the name (1-255 characters, no control characters, unique) and build the schema.

`DELETE /api/companies/{id}` **deactivates** the company: it and all of its users become inactive
and their sessions are revoked. The tenant schema and its data are kept, and a superuser can
reactivate the company with `PUT` (`"is_active": true`); its users are then re-enabled one by one.
Dropping a tenant's data is an operator action on the database, not an API call. A superuser
cannot deactivate their own company.
