<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0033 — Tenant lifecycle: provisioning through one privileged function, offboarding from every store

- **Status:** Proposed
- **Date:** 2026-10-13; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant, the one tenant mapping), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (suspending a company ends its sessions), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (the per-tenant SQL identity), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md), [ADR-0020](ADR-0020-notebook-per-user-persistence.md), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (tenant data outside the database), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (backups a deletion must reach), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (the per-tenant schema provisioning creates)

## Context

A tenant is a registry entry plus its own schema, tables, grants, a per-tenant read-only principal and
the SQL proxy's per-tenant login identity. Letting an internet-reachable service create schemas and roles
would break the per-service least privilege the platform is built on (ADR-0002, ADR-0003). Provisioning
and status changes therefore run as privileged database functions that the API's service identity may
execute without holding any schema- or role-creating privilege. Each refuses to run while the shared
namespace it resolves names in holds objects of an untrusted owner. The per-tenant login identity is
created without a credential; the SQL proxy installs one on first use through a privileged function that
is all its own identity may execute.

The product's delete action suspends: the company is marked inactive, its users are deactivated, and
their sessions and derived notebook capabilities end (ADR-0004 dec 2). Nothing is dropped. Tenant data
also lives outside the database, in the cold layer, the tracking store, per-user volumes, the capability
store and backups; an end-of-tenant decision that does not name each of them misses the newest.

## Decision

1. **A tenant is provisioned by one privileged database function, and only through it.** The function is
   owned by a privileged role and creates, in one transaction, the registry entry, schema, tables, grants,
   per-tenant read-only principal and the SQL proxy's login identity (ADR-0015 dec 5). Callers may execute
   it and hold no schema- or role-creating privilege; the platform generates the tenant identifier.
   Building a tenant's database objects for a given identifier is idempotent; a request for a *new* tenant
   always generates a new identifier.
2. **Stores outside the database are tenant-scoped by construction and created on first use.** Object
   prefixes, tracking namespaces and per-user volumes derive from the tenant identifier through each
   mediator's one mapping. Provisioning does not pre-create them, so a tenant cannot be half-provisioned
   across stores.
3. **Offboarding has two steps: suspend, then delete.** *Suspend* destroys nothing and is reversible: the
   tenant's sessions and capabilities stop being honoured, no new ones are issued, authentication refuses
   its users, and its per-tenant database identity is disabled with its open sessions ended once the
   suspension commits. Resuming re-enables the identity and restores no session. *Delete* is a separate,
   irreversible request, valid only for a suspended tenant.
4. **Delete removes the tenant from every store that holds its data.** Schema and principals, registry
   entry, cold-layer prefix, tracking namespace and artifacts, users and their notebook volumes, and
   capability entries, in one privileged, resumable operation that records what it removed. Backups age
   the tenant out on their own schedule unless a faster mechanism is decided.
5. **Removing the registry row alone is not offered.** It makes the data unfindable without making it
   gone. Every surface that ends a tenant goes through decisions 3 and 4.
6. **Lifecycle actions are privileged and recorded.** Provisioning, suspension and deletion are
   platform-operator actions, not tenant self-service; each records who did what to which tenant and when.

## Alternatives

| Alternative | Why rejected |
|---|---|
| The API service creates schemas and roles directly | Gives an internet-reachable service the most dangerous database privilege; an execute grant on a privileged function does the same job |
| Provision every store eagerly at signup | Prefixes and namespaces cost nothing until written; a multi-store creation step can half-fail |
| Soft delete only | Is decision 3's suspend step; as the only option, a request to delete a tenant's data can never be honoured |
| Delete synchronously in the API request | Spans stores with different latencies and failure modes; a timeout leaves the partial state decision 4 forbids |

## Consequences

**Positive**
- API provisioning works without widening any service's database privilege.
- "Delete this tenant" covers every store, so data does not outlive the decision.
- An operator can stop a tenant's access at once without committing to destroying its data.

**Negative**
- Every new tenant-scoped store owes a deletion path in the record that introduces it.
- The privileged functions are high-value code: a bug in them affects every tenant.
- Until backups support per-tenant removal, a deleted tenant's data persists for the backup retention.
- Delete (decision 4) is not built; the product's delete action suspends.

## Deferred

| Open question | Settled by |
|---|---|
| Grace period between suspension and deletion; scheduled or immediate deletion | Maintainer |
| Tenant data export on exit: format and scope | Maintainer |
| Deletion from backups: expiry versus per-tenant keys destroyed on delete | Jointly with ADR-0032 |
| Who may trigger each step beyond the platform operator, including a tenant-admin request flow | Maintainer |
| Legal holds that block deletion | Maintainer |
| Reconciling orphans left by the earlier registry-only delete | Maintainer |
| Whether the product's delete action keeps that name while it suspends, and how suspend and delete are presented | Maintainer, when decision 4 is built |
| Whether a structured log entry per lifecycle action is decision 6's record, or the record must be durable and queryable beside the registry | Maintainer |

## References

- [infrastructure/timescaledb/init-scripts/README.md](../infrastructure/timescaledb/init-scripts/README.md):
  the provisioning functions and operator tooling.
- `services/api_gateway/routers/companies.py`: the company lifecycle actions and their records.
- `infrastructure/timescaledb/tests/test_tenant_creation_completeness.py`: provisioning completeness.
