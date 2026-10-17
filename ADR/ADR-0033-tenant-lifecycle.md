<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0033 — Tenant lifecycle: provisioning through one privileged function, offboarding from every store

- **Status:** Proposed
- **Date:** 2026-10-13; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant, the tenant mapping), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (dec 2: suspension ends sessions), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (dec 5: per-tenant SQL identity), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md), [ADR-0020](ADR-0020-notebook-per-user-persistence.md), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (tenant data outside the database), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (backups), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (the tenant schema)

## Context

A service that creates schemas and roles itself holds the most dangerous privilege in the database. A
tenant's data also lives in the cold layer, tracking store, per-user volumes, capability store and backups,
so a registry-only action orphans the rest. Stopping access must be reversible; destroying data, deliberate.

## Decision

1. **A tenant is provisioned by one privileged database function, and only through it.** In one transaction
   it creates registry entry, schema, tables, grants, read-only principal and SQL proxy login identity, for
   callers with no schema- or role-creating privilege. The platform generates the identifier; rebuilding a tenant's objects is idempotent.
2. **Stores outside the database are tenant-scoped by construction and created on first use.** They derive
   from the tenant identifier through each mediator's one mapping, so none can be half-provisioned.
3. **Offboarding has two steps: suspend, then delete.** *Suspend* is reversible: no session, capability or
   database identity of the tenant is honoured or issued, and its open database sessions end. *Delete* is a
   separate, irreversible request, valid only for a suspended tenant.
4. **Delete removes the tenant from every store that holds its data.** One privileged, resumable operation
   removes it from every store and records what it removed; backups age it out on their own schedule.
5. **Removing the registry row alone is not offered.** Ending a tenant goes through decisions 3 and 4.
6. **Lifecycle actions are privileged and recorded.** Provisioning, suspension and deletion are
   platform-operator actions, each recording who did what to which tenant and when.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The API service creates schemas and roles directly | An execute grant on a privileged function does the same without the privilege |
| 1 | The API inserts the registry entry and an operator script run as the database superuser creates the schema | A tenant is unusable until a manual step, and routine provisioning needs the superuser |
| 2 | Provision every store eagerly at signup | Prefixes cost nothing until written; a multi-store creation can half-fail |
| 3 | Soft delete only | A request to delete a tenant's data could never be honoured |
| 4 | Delete synchronously in the API request | A timeout across stores leaves the partial state decision 4 forbids |
| 5 | Delete by removing the registry entry only | The tenant's data in every store becomes unfindable without being gone |

## Consequences

**Positive**
- API provisioning works without widening any service's database privilege.
- An operator can stop a tenant's access at once without destroying its data.

**Negative**
- Every new tenant-scoped store owes a deletion path.
- A bug in the privileged functions affects every tenant.
- Delete is not built; the product's delete action suspends.

## Deferred

| Open question | Settled by |
|---|---|
| Grace period between suspension and deletion; scheduled or immediate deletion | Maintainer |
| Tenant data export on exit: format and scope | Maintainer |
| Deletion from backups: expiry versus per-tenant keys destroyed on delete | Jointly with ADR-0032 |
| Who may trigger each step, including a tenant-admin request flow | Maintainer |
| Legal holds that block deletion | Maintainer |
| Reconciling orphans left by the earlier registry-only delete | Maintainer |
| Naming and presentation of suspend and delete in the product | Maintainer, when decision 4 is built |
| Whether a structured log entry is decision 6's record, or it must be durable and queryable | Maintainer |

## References

- [init-scripts README](../infrastructure/timescaledb/init-scripts/README.md) (provisioning); `services/api_gateway/routers/companies.py` (lifecycle actions).
