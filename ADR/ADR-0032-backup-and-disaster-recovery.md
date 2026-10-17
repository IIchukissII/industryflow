<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0032 — Backup and disaster recovery for every durable store

- **Status:** Proposed
- **Date:** 2026-10-13; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant: a backup holds every tenant), [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (where stores and their backups live), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (the capability store is not backed up), [ADR-0020](ADR-0020-notebook-per-user-persistence.md) (notebook volumes), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (the cold layer as the only copy of raw history), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (the test is the gate), [ADR-0029](ADR-0029-spark-major-upgrade-checkpoint-cutover.md) (checkpoint loss versus reprocessing), [ADR-0033](ADR-0033-tenant-lifecycle.md) (deleting a tenant from backups), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (forward-only migrations)

## Context

A scheduled job dumps the platform and tracking databases and the cluster's role definitions to an
object store, prunes old copies, and mirrors the cold bucket append-only with a principal that can only
read it. A restore tool reloads a dump using the time-series extension's restore protocol. By default the
target is the deployment's own object store, which shares the failure domain of what it protects.

Past the hot horizon the cold layer is the only copy of raw history (ADR-0025), and a database dump does
not contain it. Model artifacts live only in the object store, so a registry restored without them points
at nothing. Authored notebooks live on per-user volumes that nothing copies. Some state needs no backup:
capability handles are short-lived and re-minted (ADR-0015), the broker is transport (ADR-0005), and
streaming checkpoints are rebuilt by reprocessing (ADR-0029).

Under schema-per-tenant a backup contains every tenant's data, which makes it the most privileged
artifact the platform produces.

## Decision

1. **Every durable store is classified, and the class decides its recovery path.** An *authoritative*
   store holds data that exists nowhere else (platform database, tracking database, cold layer, model
   artifacts, authored notebooks) and is backed up. A *derivable* store is rebuilt from an authoritative
   one or is ephemeral by design (capability store, broker topics, streaming checkpoints, caches); it is
   not backed up and its rebuild path is recorded. The record that introduces a store classifies it.
2. **Recovery targets are declared per authoritative store.** Each has a recovery-point and recovery-time
   objective, owned by the operations documentation. A store without them is a gap, not a default.
3. **Disaster recovery means a copy outside the failure domain.** A backup that shares storage, cluster
   or site with what it protects is *undo*. A deployment that claims disaster recovery keeps at least one
   copy of every authoritative store in a separate failure domain.
4. **The cold layer is backed up as an object store, not as a database.** Its objects are copied to a
   separate failure domain and kept as long as the cold layer keeps them. The hot store drops only what
   the cold layer has verified, so a drop is safe only while that only copy is also protected.
5. **A backup is held to the standard of the most privileged store it contains.** It is written by a
   principal that can write backups and nothing else, is unreachable from any tenant-facing plane, and is
   protected at rest where the target allows. No capability, kernel or tenant session can read it.
6. **A restore is proven by doing it.** Restores are rehearsed on a schedule against a scratch target and
   compared with the source; an unexercised backup does not count toward a recovery objective.
7. **A schema rollback is a restore.** Migrations are forward-only (ADR-0034); the way back from a bad
   migration is the backup taken before it, so a backup precedes every upgrade.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Back up the databases only | Misses the cold layer and model artifacts, which the databases no longer hold |
| Rely on the object store's own replication | In-cluster replication shares the failure domain and copies deletions; a managed store with versioning and cross-region replication is one way to meet decision 3, not a reason to skip it |
| One recovery objective for the whole platform | Over-protects derivable state or under-protects the only copy of raw history |
| Back up derivable state too | Puts live, revocable handles into a long-lived artifact (ADR-0015 dec 7) and preserves checkpoints ADR-0029 may discard |

## Consequences

**Positive**
- The cold layer's export-then-drop argument holds: the only copy is itself protected.
- Each store has one recorded answer to whether it is backed up.
- A deployment knows whether it has undo or disaster recovery.

**Negative**
- Off-site copies cost storage and transfer, and the cold copy grows without bound.
- Every backup holds every tenant's data and is one more place tenant deletion must reach (ADR-0033).
- Restore rehearsal is recurring operational work that needs an owner.

## Deferred

| Open question | Settled by |
|---|---|
| Concrete objectives and schedules per store | Operations configuration |
| Continuous archiving and point-in-time recovery for the database: tool and operation | Maintainer |
| Backup mechanism and cadence for model artifacts and authored notebooks | Maintainer |
| The cold mirror's off-site target and its retention | Operations configuration, under decisions 3 and 4 |
| Encryption and key management at rest beyond what the target provides | Maintainer |
| Tenant-granular restore | Maintainer |
| Deleting a tenant from backups: expiry versus per-tenant keys | Jointly with ADR-0033 |

## References

- [docs/operations/backup-and-recovery.md](../docs/operations/backup-and-recovery.md): coverage, job
  configuration, restore runbook.
- `infrastructure/backup/`: the backup and restore scripts.
- ADR-0025 dec 13: the cold mirror's principals.
