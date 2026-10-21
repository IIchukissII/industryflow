<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0033 — Tenant lifecycle: provisioning through one privileged function, offboarding from every store

- **Status:** Proposed
- **Date:** 2026-10-13; changed 2026-10-21
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant, the tenant mapping), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (dec 2: suspension ends sessions), [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (dec 3: refused readings are dead-lettered), [ADR-0007](ADR-0007-public-key-infrastructure.md) (device revocation), [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) (tracking store), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (dec 5: per-tenant SQL identity), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md), [ADR-0020](ADR-0020-notebook-per-user-persistence.md), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (tenant data outside the database), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (backups), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (the tenant schema)

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
3. **Offboarding has two steps: suspend, then delete.** *Suspend* is reversible: no session, capability,
   device or database identity of the tenant is honoured or issued, and its open database sessions end.
   Ingestion refuses the tenant's devices from a tenant-status stream the platform publishes on the
   broker, the only store it reaches, and every component that writes tenant data refuses an inactive
   tenant's readings from the registry, so a lost or late status fails closed. *Delete* is a
   separate, irreversible request, valid only for a suspended tenant.
4. **Delete removes the tenant from every store that holds its data.** One privileged, resumable operation,
   run by a dedicated operator job and not by the API, removes it store by store and records each removal.
   Each store is emptied through its own mediator, the tracking store through its API and the notebook
   hub for its users' volumes, and every device certificate of the tenant is revoked. Backups and the
   broker's retention age the tenant out on their own schedule, and the record states when.
5. **Removing the registry row alone is not offered.** Ending a tenant goes through decisions 3 and 4.
6. **Lifecycle actions are privileged and recorded.** Provisioning, suspension and deletion are
   platform-operator actions, each recording who did what to which tenant and when. The record is durable,
   queryable and held outside every tenant's schema, so it outlives the tenant.
7. **Delete is an explicit operator request after at least 30 days of suspension.** Nothing schedules it;
   the wait leaves a mistaken suspension recoverable.
8. **A deleted tenant's registry entry stays, as a tombstone.** It anchors the deletion record, keeps the
   identifier from reuse, and lets every verifier refuse the tenant as deleted rather than unknown.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The API service creates schemas and roles directly | An execute grant on a privileged function does the same without the privilege |
| 1 | The API inserts the registry entry and an operator script run as the database superuser creates the schema | A tenant is unusable until a manual step, and routine provisioning needs the superuser |
| 2 | Provision every store eagerly at signup | Prefixes cost nothing until written; a multi-store creation can half-fail |
| 3 | Soft delete only | A request to delete a tenant's data could never be honoured |
| 3 | Revoke the tenant's device certificates at suspension | Revocation is permanent, so suspension would stop being reversible |
| 3 | Enforce suspension only where access is granted | The writers that read the broker directly would keep accepting the tenant's data |
| 3 | Ingestion reads the capability store's suspension marker | Gives a device-facing service reach to a store that holds every tenant's live handles |
| 3 | No refusal at ingestion, only at the writers | A suspended tenant's devices keep loading the shared broker for the whole suspension |
| 4 | Delete synchronously in the API request | A timeout across stores leaves the partial state decision 4 forbids |
| 4 | Delete in a background task of the API | Ties the operation's durability to the lifetime of an API process |
| 4 | Purge the tracking store's rows directly | Bypasses the store's own lifecycle and risks dangling references in a store all tenants share |
| 4 | The delete job holds a notebook-hub administrator credential | ADR-0020 rejects a standing hub credential that crosses tenants |
| 4 | Purge the tenant's messages from the shared broker | Needs administrative writes to a shared cluster for data that ages out within its retention |
| 4 | Per-tenant backup keys destroyed on delete | Needs key management the platform does not have; expiry meets decision 4 |
| 5 | Delete by removing the registry entry only | The tenant's data in every store becomes unfindable without being gone |
| 6 | A structured log entry as the record | Log retention can drop it, and it cannot be queried once the tenant is gone |
| 7 | Scheduled deletion when the wait ends | Makes an irreversible action happen without a decision |
| 7 | Delete immediately on request | Leaves no way back from a mistaken suspension |
| 8 | Remove the registry entry once every store is empty | Loses the deletion record's anchor and makes a deleted tenant indistinguishable from an unknown one |

## Consequences

**Positive**
- API provisioning works without widening any service's database privilege.
- An operator can stop a tenant's access at once without destroying its data.

**Negative**
- Every new tenant-scoped store owes a deletion path.
- A bug in the privileged functions affects every tenant.
- Delete is not built; the product's delete action suspends.
- A deleted tenant's data persists in backups and broker retention until each ages it out.
- Reactivating a tenant after delete is impossible by design, including its device certificates.

## Deferred

| Open question | Settled by |
|---|---|
| Tenant data export on exit: format and scope | Maintainer |
| A tenant-admin request flow for deletion | Maintainer, after decision 4 is built |
| Legal holds that block deletion | Maintainer |
| Reconciling orphans left by the earlier registry-only delete | Maintainer, after decision 4 is built |
| Naming and presentation of suspend and delete in the product | Maintainer, when decision 4 is built |
| Per-tenant backup keys | Jointly with ADR-0032's encryption deferral |

## References

- [init-scripts README](../infrastructure/timescaledb/init-scripts/README.md) (provisioning); `services/api_gateway/routers/companies.py` (lifecycle actions).
