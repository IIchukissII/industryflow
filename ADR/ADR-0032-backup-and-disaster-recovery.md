<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0032 — Backup and disaster recovery for every durable store

- **Status:** Accepted
- **Date:** 2026-10-24
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (a backup holds every tenant), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (capability store, dec 7), [ADR-0020](ADR-0020-notebook-per-user-persistence.md) (notebook volumes), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (cold layer, the only copy of raw history; dec 13 its mirror), [ADR-0029](ADR-0029-spark-major-upgrade-checkpoint-cutover.md) (checkpoints), [ADR-0033](ADR-0033-tenant-lifecycle.md) (tenant deletion), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (forward-only migrations)

## Context

The cold layer past the hot horizon, model artifacts and authored notebooks exist nowhere else, and no
database dump holds them. A backup in its source's storage, cluster or site does not survive losing that
domain. Under schema-per-tenant every backup holds every tenant's data.

## Decision

1. **Every durable store is classified, and the class decides its recovery path.** *Authoritative* stores
   (databases, cold layer, model artifacts, authored notebooks) are backed up; *derivable* ones (capability
   store, broker topics, streaming checkpoints, caches) record a rebuild path instead.
2. **Recovery targets are declared per authoritative store.** The operations documentation owns the
   recovery-point and recovery-time objectives; a store without them is a gap.
3. **Disaster recovery means a copy outside the failure domain.** A backup sharing storage, cluster or site
   with its source is *undo*; disaster recovery needs a copy of every authoritative store elsewhere.
4. **The cold layer is backed up as an object store, not as a database.** Its objects are copied to a
   separate failure domain for as long as the cold layer keeps them. An object the cold layer no longer
   holds leaves the copy once one backup retention period has passed, so an accidental deletion stays
   recoverable that long and a deleted tenant's history does not outlive it.
5. **A backup is held to the standard of the most privileged store it contains.** A write-only backup
   principal writes it; no capability, kernel, tenant session or tenant-facing plane can read it.
6. **A restore is proven by doing it.** Restores are rehearsed on a schedule against a scratch target and
   compared with the source; an unexercised backup counts toward no recovery objective.
7. **A schema rollback is a restore.** Migrations are forward-only, so a backup precedes every upgrade and
   is the way back from a bad migration.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Back up the databases only | Misses the cold layer and model artifacts, which exist nowhere else |
| 1 | Back up the capability store | Puts live, revocable handles into a long-lived artifact |
| 1 | Back up streaming checkpoints | Keeps state that a major upgrade may deliberately discard |
| 2 | One recovery objective for the whole platform | Over-protects derivable state or under-protects the only copy of raw history |
| 3 | Rely on the object store's own replication | In-cluster replication shares the failure domain and copies deletions; managed cross-region versioned replication is one way to meet decision 3 |
| 4 | Keep the cold copy append-only | A deleted tenant's raw history would never leave it (ADR-0033 dec 4) |

## Consequences

**Positive**
- Hot-store drops are safe because the cold layer's only copy is protected.
- A deployment knows whether it has undo or disaster recovery.

**Negative**
- Off-site copies cost storage and transfer; the cold copy grows without bound.
- Restore rehearsal is recurring operational work that needs an owner.

## Deferred

| Open question | Settled by |
|---|---|
| Objectives, schedules, off-site target and retention per store, including the cold mirror | Operations configuration |
| Continuous archiving and point-in-time recovery for the database | Maintainer |
| Backup mechanism and cadence for model artifacts and authored notebooks | Maintainer |
| Encryption and key management at rest beyond the target's own | Maintainer |
| Tenant-granular restore | Maintainer |
| Per-tenant backup keys destroyed on delete, beside expiry | Jointly with ADR-0033, with the encryption question above |

## References

- [docs/operations/backup-and-recovery.md](../docs/operations/backup-and-recovery.md) (runbook); `infrastructure/backup/` (scripts).
