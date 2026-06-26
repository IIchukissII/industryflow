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
| `engineer` | Manage equipment, sensors, alert rules, models within the tenant |
| `admin` | Tenant administration within their **own** company |
| *superuser* (`is_superuser=true`) | Cross-tenant / platform operations |

`role` is one of `observer | engineer | admin` (a DB CHECK constraint enforces it);
`is_superuser` is a separate flag. A superuser passes any role gate.

## Tenant scoping (ADR-0004 decision 5)

Authorization is scoped to the caller's company. A per-tenant **admin acts only on their
own company**; **cross-company actions require a superuser**:

| Endpoint | Per-tenant admin | Superuser |
|----------|------------------|-----------|
| `GET /api/users` | own company's users | all users |
| `GET /api/companies`, `GET /api/companies/{id}` | own company only | any |
| `PUT /api/companies/{id}` | own company only (404 otherwise) | any |
| `POST /api/companies`, `DELETE /api/companies/{id}` | forbidden | allowed (provisioning) |

Requests that target another tenant's company return **404** (so other tenants' IDs are
not confirmed). Every data query is constrained to the authenticated tenant.

## Managing users

Create or manage users with the CLI helper, or the API:

```bash
# CLI (run inside the api-gateway container or against the DB)
python3 scripts/manage_users.py            # see --help for create / list / set-role

# API: register a user (admin/superuser)
curl -X POST http://localhost:8000/auth/register \
  -H "Authorization: Bearer <access_token>" -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "...", "company_id": "<uuid>", "role": "engineer"}'
```

Tenants (companies) are provisioned by a superuser via `POST /api/companies`, which creates
the company and its isolated `tenant_<uuid>` schema (see
**[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)** and the
[database init scripts](../../infrastructure/timescaledb/init-scripts/README.md)).
