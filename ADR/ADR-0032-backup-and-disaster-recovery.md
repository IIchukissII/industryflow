<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0032 — Backup and disaster recovery: every durable store has a recovery path, and only-copies leave the failure domain

- **ID:** ADR-0032
- **Status:** Proposed
- **Date:** 2026-10-13
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (the in-cluster/external toggle that decides where stores and their backups live), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (the cold layer, which becomes the only copy of raw history), [ADR-0033](ADR-0033-tenant-lifecycle.md) (deleting a tenant from its backups), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (forward-only migrations, whose rollback is a restore)
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant, which makes a backup a multi-tenant artifact), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (the test is the gate — applied to restores)

## Context and problem

The platform backs up its databases and its cold bucket and nothing else, and no record says why that set is the right one.

What exists was built as operations work, not decided: a scheduled job dumps each database and the
cluster's role definitions to the object store and prunes old copies, the same job mirrors the cold
bucket, and a restore tool reloads a dump
with the time-series extension's restore protocol. By default the target is the deployment's own object
store — the same cluster, the same storage, the same failure domain as the database it protects. A
continuous-archiving tier for point-in-time recovery is described as planned. That is a reasonable first
tier, and it protects against the failure it was built for: a bad write or an accidental drop.

What it does not protect against has grown since it was built:

- **The cold layer is now the only copy of raw history.** ADR-0025 shortened the hot store's raw
  retention and made the export → verify → drop ordering the only thing between an ageing chunk and
  lost data. Past the hot horizon, the Parquet in the object store is not a copy of anything; it is the
  data. ADR-0025's negative consequences say so in as many words. A database dump does not contain it.
  The backup job now mirrors the cold bucket — append-only, reading it with a principal that cannot
  write or delete it (ADR-0025 dec 13) — but by default into the same object store, and a backup that
  lives in the same object store does not survive the loss of that store.
- **Model artifacts live only in the object store.** The tracking database records runs and versions;
  the bytes of every registered model live under the tracking store's prefix. A database restore without
  them restores a registry that points at nothing.
- **Authored notebooks live on per-user volumes** (ADR-0020). Nothing copies them.
- **Default backups share the failure domain** of what they protect, so on the default configuration
  there is no disaster recovery at all, only undo.

Some state needs no backup, and the record should say which, so that its absence is a decision rather
than an oversight: the capability store holds only short-lived, revocable handles that are re-minted
(ADR-0015); the message broker is transport, not a store of record (ADR-0005); streaming checkpoints can
be rebuilt by reprocessing within the broker's retention, at the cost ADR-0029 already weighs.

The cold bucket's mirror was added before this record; this record gives it, and the gaps it does not
close, a decision to conform to. Under decision 4 the mirror is the right shape; under decision 3 it is
undo until its target is a separate failure domain.

## Decision drivers

- **An only-copy is the dangerous class.** Losing a copy of something costs a rebuild; losing the only
  copy costs the data. The cold layer moved raw history into that class on purpose.
- **A backup in the same failure domain is undo, not recovery.** It survives a bad statement; it does not
  survive the loss of a disk, a cluster or a site.
- **Recovery targets differ by what is lost.** Minutes of measurements, a day of alert history and a
  year of raw history do not have the same cost of loss or the same cost of protection. One platform-wide
  number would be wrong for most of them.
- **A backup holds every tenant.** Under schema-per-tenant a database dump contains all tenants' data;
  it is the most privileged artifact the platform produces and must be held like one.
- **An untested backup is a hope.** The release model's rule — the test is the gate (ADR-0026) — applies
  to restores as much as to builds.

## Decision

1. **Every durable store is classified, and the class decides its recovery path.** A store is either
   *authoritative* — it holds data that exists nowhere else (the platform database, the tracking database,
   the cold layer, model artifacts, authored notebooks) — and is backed up; or *derivable* — it can be
   rebuilt from an authoritative store or is ephemeral by design (the capability store, the message
   broker's topics, streaming checkpoints, caches) — and is not backed up, with its rebuild path recorded
   instead. A new store is classified by the record that introduces it.

2. **Recovery targets are declared per class.** Each authoritative store has a stated recovery-point and
   recovery-time objective. The values are operational configuration, owned by the operations
   documentation, not by this record; what this record fixes is that every authoritative store has them,
   and that a store without them is a gap, not a default.

3. **Disaster recovery means a copy outside the failure domain.** A backup that shares storage, cluster
   or site with what it protects is recorded and operated as *undo*. A deployment that claims disaster
   recovery keeps at least one copy of every authoritative store in a separate failure domain. The
   default in-deployment target remains valid for undo and is documented as exactly that.

4. **The cold layer is backed up as an object store, not as a database.** Its backup is a copy of its
   objects to a separate failure domain, kept for as long as the cold layer keeps them (ADR-0025's
   retention is unbounded). Because the hot store drops what the cold layer has verified, the cold copy's
   own durability is part of the export's safety argument: a drop is safe only while the only copy is
   also protected.

5. **A backup is held to the standard of the most privileged store it contains.** It is written by a
   principal that can write backups and nothing else, is not reachable from any tenant-facing plane, and
   is protected at rest wherever the target allows. No capability, kernel or tenant session can read it.

6. **A restore is proven by doing it.** Restores are rehearsed on a schedule against a scratch target and
   compared with the source; a backup whose restore has not been exercised is not counted toward a
   recovery objective.

7. **A schema rollback is a restore.** Migrations are forward-only (ADR-0034); the way back from a bad
   migration is the most recent backup taken before it, which is why a backup precedes every upgrade.

## Alternatives considered

**A. Keep backing up the databases only, as today.** *Rejected:* it was right when the database held all
authoritative data. Since ADR-0025 it does not, and since model artifacts moved to the object store it
did not for them either. A database-only backup now restores a registry whose models are missing and a
history whose oldest years are gone.

**B. Rely on the object store's own replication for everything in it.** *Rejected as the rule:* in-cluster
replication shares the cluster's failure domain, and replication copies a deletion as faithfully as a
write — it protects against a failed disk, not a bad delete. A managed store with versioning and
cross-region replication can satisfy decision 3 for its contents; that is a way to meet the decision, not
a reason to skip it.

**C. One recovery objective for the whole platform.** *Rejected:* it either over-protects cheap,
derivable state or under-protects the only copy of raw history, and it hides which store set the number.

**D. Back up derivable state too, for simplicity.** *Rejected:* backing up the capability store would put
live, revocable handles into a long-lived artifact, against ADR-0015 dec 7's bound on what a store reader
can lose; backing up checkpoints would preserve exactly the state ADR-0029 may choose to discard.

## Consequences

### Positive

- The cold layer's safety argument closes: verified export, then drop, with the only copy itself protected.
- "Do we back that up?" has one answer per store, recorded where the store is introduced.
- Undo and disaster recovery stop being confused; a deployment knows which one it has.

### Negative

- Off-site copies cost storage and transfer, and the cold layer's copy grows without bound, as the cold
  layer does.
- More artifacts now hold every tenant's data, and each one is a place tenant deletion must reach
  (ADR-0033).
- Restore rehearsal is recurring operational work that has to be scheduled and owned.

## Deferred decisions

- **The concrete objectives and schedules** per store — operations configuration.
- **Continuous archiving / point-in-time recovery** for the database: the tool and its operation.
- **Backups of model artifacts and authored notebooks:** the mechanism and its cadence.
- **The cold mirror's off-site target:** the mirror exists; which separate failure domain a deployment
  points it at, and its retention, are operations configuration under decisions 3 and 4.
- **Encryption and key management** for backups at rest, beyond what the target store provides.
- **Tenant-granular restore:** restoring one tenant's schema and objects without the others.
- **Deleting a tenant from backups:** expiry versus per-tenant keys — owed jointly with ADR-0033.

## References

- `docs/operations/backup-and-recovery.md` — what the backup job does today, its configuration, and the
  restore runbook.
- ADR-0025 — the cold layer and its "only copy of long history" consequence.
- ADR-0020 — per-user notebook volumes.
- ADR-0015 dec 7 — why the capability store's contents must not outlive their handles.
- ADR-0029 — checkpoint loss versus reprocessing.
