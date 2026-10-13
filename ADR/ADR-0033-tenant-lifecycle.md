<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0033 — Tenant lifecycle: provisioned by one privileged function, offboarded from every store

- **ID:** ADR-0033
- **Status:** Proposed
- **Date:** 2026-10-13
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant and the one tenant mapping), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (backups, which a deletion must also reach), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (the per-tenant schema a tenant is provisioned with)
- **Related:** [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (the per-tenant read-only identity, which a tenant now owns), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) / [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) / [ADR-0020](ADR-0020-notebook-per-user-persistence.md) (tenant data outside the database)

## Context and problem

The platform isolates tenants carefully and has never decided how one begins or ends.

**Beginning.** A tenant is a registry row plus a schema of its own, with its tables, grants and a
per-tenant read-only principal. The database already carries one function that creates all of that, and
an operator script calls it with the database superuser. The product's own "create company" action,
however, inserted only the registry row: a tenant created through the API had no schema, and every
request for it failed until an operator ran the script by hand. The obvious repair — have the API
service create the schema itself — would give a long-running, internet-reachable service the right to
create schemas and roles in the database of record, which is the opposite of the per-service least
privilege the platform is built on (ADR-0002, ADR-0003). API provisioning now goes through privileged
database functions instead: the API's service identity may execute them and holds no schema- or
role-creating privilege of its own. One provisions a tenant — validating its name, generating its
identifier, and creating its registry entry, schema, tables and grants in one transaction — and one
changes a tenant's name or active flag; each refuses to run while the shared namespace it resolves
names in contains objects owned by an untrusted role, so it cannot be steered into calling one. The
SQL proxy's per-tenant login identity (ADR-0015 rev 2) is created there too, without a credential. The
proxy installs the credential on first use, through a privileged function of its own that is all its
database identity may execute; for a tenant provisioned before provisioning created the identity, that
function creates it.

**Ending.** The product's "delete company" action used to remove the registry row and nothing else. The
schema, its data, the per-tenant read-only principal, the tenant's cold-layer prefix, its namespace in
the tracking store and its model artifacts, its users' notebook volumes — all survived, unreachable by
the tenant and invisible to the platform, which no longer had a registry row to find them by. That is
not deletion; it is orphaning. It now **deactivates** instead: the company is marked inactive, every one
of its users is deactivated, and all of their sessions — and the notebook capabilities derived from them
— are ended (ADR-0004 dec 2 rev 2). Nothing is dropped and the registry row stays, so nothing is
orphaned. That is decision 3's suspend step, offered under the name of a delete. Clearing the
company's active flag directly takes the same path: its users' sessions and capabilities are ended,
every authentication reads the company's flag and refuses its users while it is clear, no capability
for it is issued or honoured, and its per-tenant database identity is disabled, its open sessions
ended once the suspension is committed. Setting the flag again restores the identity and ends the
refusal; it restores no session. Delete in decision 4's sense does not exist.

Tenant data now lives in more places than the schema: the cold layer (ADR-0025), the tracking store
(ADR-0019), per-user volumes (ADR-0020), the capability store, and backups (ADR-0032). Any decision about
ending a tenant has to name all of them, or it will quietly miss the newest.

## Decision drivers

- **Least privilege does not stop at provisioning.** A service that can create a tenant must not thereby
  hold the right to create, alter or drop arbitrary schemas and roles.
- **A tenant is all of its stores, not its registry row.** Lifecycle actions that touch one store and
  leave the rest are how data outlives the decision to delete it.
- **Suspension and deletion are different requests.** Stopping access must be fast and reversible;
  destroying data must be deliberate and irreversible. Collapsing them forces a choice between the two
  every time.
- **The tenant identifier is a security boundary** (ADR-0003, ADR-0019): it must be generated and
  validated by the platform, never accepted from a caller, and never reused.

## Decision

1. **A tenant is provisioned by one privileged database function, and only through it.** The function
   is owned by a privileged role and creates, in one transaction, everything a tenant has in the
   database: its registry entry, its schema and tables, its grants, its per-tenant read-only principal
   and the login identity the SQL proxy uses for it (ADR-0015 rev 2). Services and operator tooling may
   execute it and hold no schema- or role-creating privilege of their own. The tenant identifier is
   generated by the platform. Provisioning is idempotent: re-running it for an existing tenant converges
   rather than fails or duplicates. The step that converges is the one that builds a tenant's database
   objects for a given identifier; the request that provisions a *new* tenant generates a new identifier
   each time, so it is never the way to re-run provisioning for one that exists.

2. **Stores outside the database are tenant-scoped by construction and created on first use.** Object
   prefixes, tracking namespaces and per-user volumes are derived from the tenant identifier by the one
   mapping each mediator already uses, and come into being when first written. Provisioning does not
   pre-create them, so there is no second place a tenant can be half-provisioned.

3. **Offboarding has two steps: suspend, then delete.** *Suspend* withdraws access and destroys nothing:
   sessions and capabilities for the tenant stop being honoured, new ones are not issued, and its
   per-tenant database identity is disabled and its open database sessions ended. It is reversible.
   *Delete* is a separate, deliberate request, valid only for a suspended tenant, and is irreversible.

4. **Delete removes the tenant from every store that holds its data.** The schema and its principals,
   the registry entry, the cold-layer prefix, the tracking namespace and its artifacts, the tenant's
   users and their notebook volumes, and any capability entries. It runs as one privileged operation
   that records what it removed; no store is left for a later sweep to find. Backups age the tenant out
   on their own schedule unless a faster mechanism is decided (Deferred).

5. **Removing the registry row alone is not offered.** An action that drops the tenant's entry while its
   data remains makes that data unfindable without making it gone. Any surface that ends a tenant does
   so through decisions 3 and 4.

6. **Lifecycle actions are privileged and recorded.** Provisioning, suspension and deletion are
   platform-operator actions, not tenant self-service, and each leaves a record of who did what to which
   tenant and when.

## Alternatives considered

**A. Let the API service create schemas and roles directly.** *Rejected:* it repairs API provisioning by
granting an internet-reachable service the most dangerous privilege in the database. The privileged
function gives the same result with an execute grant.

**B. Provision every store eagerly — create the cold prefix, tracking namespace and volumes at signup.**
*Rejected:* prefixes and namespaces cost nothing until written, and eager creation adds a multi-store
transaction that can half-fail. Deriving them from the identifier makes them exist exactly when needed.

**C. Soft delete only — mark the tenant inactive and keep its data.** *Rejected as the end state:* it is
decision 3's suspend step, and useful as one; as the only option it means the platform can never honour
a request to delete a tenant's data.

**D. Delete synchronously from the API request.** *Rejected:* deletion spans stores with different
latencies and failure modes; a request that times out halfway leaves exactly the partial state decision
4 forbids. It is a privileged job that can be resumed until complete.

## Consequences

### Positive

- API provisioning works without widening any service's database privilege.
- "Delete this tenant" has a meaning that covers every store, so data does not outlive the decision.
- An operator can stop a tenant's access immediately without committing to destroying its data.

### Negative

- Deletion must be kept in step with every new tenant-scoped store; a store added without a deletion
  path reintroduces orphaning. The record that introduces a store owes its deletion path.
- The privileged functions are high-value code: a bug in them affects every tenant.
- Until backups support per-tenant removal, a deleted tenant's data persists in backups for their
  retention period, and that has to be stated to whoever asked for the deletion.

## Deferred decisions

- **Grace period** between suspension and deletion, and whether deletion is scheduled or immediate.
- **Tenant data export on exit** — format and scope.
- **Deletion from backups:** expiry versus per-tenant encryption keys destroyed on delete (with ADR-0032).
- **Who may trigger each step** beyond "platform operator", including any tenant-admin request flow.
- **Legal holds** that must block deletion.
- **Reconciling existing orphans** left by the registry-only delete.
- **Renaming the existing action.** The action the product calls "delete" now suspends (Context);
  whether it keeps that name until decision 4 exists, and how the two are presented, is open.
- **Where decision 6's record lives.** Each lifecycle action now leaves a structured log entry naming
  the actor, the action and the tenant. Whether a log entry is the record decision 6 asks for, or the
  record must be durable and queryable alongside the registry, is not yet decided.

## References

- ADR-0003 — schema-per-tenant and the single tenant mapping every store derives from.
- ADR-0015 rev 2 — the per-tenant login identity that provisioning now creates and deletion removes.
- ADR-0019, ADR-0020, ADR-0025 — tenant data in the tracking store, per-user volumes and the cold layer.
- ADR-0032 — backups as another store a deletion must reach.
- `infrastructure/timescaledb/init-scripts/README.md` — the provisioning function and operator tooling.
