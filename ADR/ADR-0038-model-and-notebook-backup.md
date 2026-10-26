<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0038 — Model artifacts are mirrored, notebook volumes archived, by the backup job

- **Status:** Accepted
- **Date:** 2026-10-26
- **Parent:** [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (its deferred mechanism for model
  artifacts and authored notebooks)
- **Related:** [ADR-0020](ADR-0020-notebook-per-user-persistence.md) (dec 5-6: per-user volumes, owned
  and named by the hub), [ADR-0030](ADR-0030-externally-authored-model-admission.md) (dec 10: the upload
  staging area), [ADR-0033](ADR-0033-tenant-lifecycle.md) (dec 4: deleted tenants leave backups by
  ageing), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (the cold mirror this follows),
  [ADR-0014](ADR-0014-notebook-hub-single-sign-on.md) (the hub keeps no user store)

## Context

Model artifacts live only in the tracking artifact store, and authored notebooks only in per-user work
volumes; ADR-0032 makes both authoritative and leaves their mechanism open. The artifact store is an
object store like the cold layer. A work volume is storage of the container runtime, named and owned by
the hub. The backup job already reads the cold layer with a credential the tenant-facing cold broker
also holds.

## Decision

1. **Model artifacts are mirrored into the backup target the way the cold layer is.** The copy is
   incremental, verified per object, and an object gone from the source leaves the mirror after one
   retention period (ADR-0032 dec 4), so a deleted tenant's models do not outlive it (ADR-0033 dec 4).
2. **The upload staging area is not backed up.** It holds unadmitted bytes that nothing loads and that
   expire on their own (ADR-0030 dec 10); they are derivable by re-uploading.
3. **Every object mirror reads with one read-only principal held by the backup job alone.** No serving,
   tracking or tenant-facing workload shares it, so a leaked backup credential reads nothing new to
   them and theirs reads no backup source they do not already serve.
4. **Each authored work volume is archived per backup run, one verified archive per volume.** Archives
   are kept by count like the dumps; once a volume no longer exists, its archives leave after one
   retention period.
5. **On a Docker host the backup job reaches the volumes through the container runtime, read-only, and
   finds them by the spawner's naming.** It mounts each into a short-lived helper with no network; it
   never reads the hub's database. ADR-0020 dec 6 keeps deletion with the hub; reading for backup does
   not change what the hub owns and cannot destroy a volume.
6. **On Kubernetes a work volume is backed up by the cluster's volume-snapshot mechanism.** It binds
   any Kubernetes deployment of the authoring profile; the chart builds it when the hub runs there.
7. **The hub's own database is derivable.** The hub keeps no user store (ADR-0014 dec 1); users and
   servers return from the platform session.
8. **A restore of each kind is rehearsed against a scratch target and compared** (ADR-0032 dec 6): the
   model mirror object for object, an archive file for file.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Back up models through the tracking server's export | A second reader of every tenant's runs, and it misses admitted uploads that have no run |
| 1 | Keep model mirror copies forever | A deleted tenant's models would never leave the backup |
| 2 | Mirror the staging area too | Carries bytes no tenant can address, against their own expiry |
| 3 | Read the artifact store with the tracking server's principal | Hands a backup workload the credential of an unauthenticated artifact proxy's backend |
| 3 | Keep reading the cold layer with the cold broker's principal | A tenant-facing plane's credential stays in the backup job, and the backup's in the broker's reach |
| 4 | Snapshot the host's volume directory | Copies every volume of the host, the database's and object store's included |
| 5 | The hub archives its own volumes | Puts a backup-writing credential into the tenant-facing hub, and a restore needs the job anyway |
| 5 | Enumerate volumes from the hub's database | Needs a hub credential outside the hub (ADR-0020 dec 6) |
| 6 | One archive mechanism for both spawners | A Kubernetes volume is not reachable through a container runtime the job can address |

## Consequences

**Positive**
- Every authoritative store has a backup path, and the cold mirror no longer shares a tenant-facing credential.

**Negative**
- The backup job on a Docker host holds container-runtime access, the same trust tier as the hub and log shipper.
- An archive of a volume in use is crash-consistent, not quiesced.
- Kubernetes notebook backup is unbuilt until the hub runs on a cluster.

## Deferred

| Open question | Settled by |
|---|---|
| Point-in-time copies of a volume in use | First lost edit an archive taken mid-write could not restore |

## References

- `infrastructure/backup/db-backup.sh` (decisions 1-3), `notebook-backup.sh`, `notebook-restore.sh` (4-5).
- `infrastructure/minio/minio-init.sh` (`backup-read`, decision 3).
- [docs/operations/backup-and-recovery.md](../docs/operations/backup-and-recovery.md) (procedures, rehearsal).
