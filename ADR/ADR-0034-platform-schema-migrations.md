<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0034 — Platform schema migrations: one ordered, idempotent sequence for fresh installs and live tenants

- **Status:** Accepted
- **Date:** 2026-10-24; changed 2026-11-13
- **Parent:** ADR-0001
- **Related:** [ADR-0017](ADR-0017-database-tls-and-authentication.md) (the runner is a network client), [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (a change applies once per tenant), [ADR-0008](ADR-0008-extension-interface-for-a-domain-generic-core.md), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extension-owned tables), [ADR-0026](ADR-0026-release-model.md) (dec 4: upgrade-path gate), [ADR-0032](ADR-0032-backup-and-disaster-recovery-for-every-durable-store.md) (dec 7: the runner refuses an upgrade without a recent backup, which is the way back), [ADR-0033](ADR-0033-tenant-lifecycle.md) (provisioning), [ADR-0009](ADR-0009-kubernetes-deployment-packaged-with-helm.md) (dec 2: a job with a single-writer checkpoint volume is replaced, never rolled), [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (dec 2, 3: offsets committed after the work is durable, so a stopped consumer resumes where it was), [ADR-0029](ADR-0029-spark-major-upgrades.md) (a stopped engine resumes its checkpoint)

## Context

Under schema-per-tenant a schema change applies to every existing tenant and to every tenant provisioned
later; missing either half fails silently, for some tenants only. The build checks only that per-tenant shape
functions are called from provisioning. Applied by hand, a live database records nothing of which scripts
it has had, and shell scripts in the sequence read secrets from the deployment. Rolling back is rarely safe
once data is written in the new shape. A migration that corrects data is undone by a writer of the
previous version still running after it: a rolled upgrade keeps the old writer until its successor is up,
which is after the sequence has run.

## Decision

1. **One ordered sequence of numbered scripts is the only definition of the platform schema.** Fresh and
   upgraded databases run it; a **migration** runs once and is never edited after release, a **definition**
   (provisioning and what it calls) is replaced in place and re-applied when changed.
2. **Every script is idempotent.** It converges where the change is missing and is a no-op where it is not.
3. **A per-tenant change has three required parts:** the change for one tenant schema, its application to
   every existing tenant, and its inclusion in provisioning. A check reading the scripts and the provisioning
   function fails the build when a part is missing.
4. **A live database is upgraded by applying the pending scripts in order, recorded in a ledger, before
   services start.** Unrecorded migrations and changed definitions are applied and recorded; services do not
   start against a database the ledger shows behind, and fresh installs write the same ledger.
5. **Migrations are forward-only.** There are no down-scripts; the way back is a restore of the backup taken
   before the upgrade, which the runner requires before it changes a database holding platform state
   unless the operator explicitly chooses to go without one (ADR-0032 dec 7).
6. **The upgrade path is tested.** The whole-stack gate applies the pending sequence to state written by
   the commit the change merges onto, settling the database under ADR-0026 dec 4; the upgrade across
   several merges is the box run's (ADR-0026 dec 8).
7. **A purpose-built runner is the only applier, on fresh and live databases alike.** It runs the scripts
   as written, shell scripts that set role passwords from the deployment's secrets included, so a fresh
   install writes the same ledger an upgrade does; the image's empty-volume init hook runs none of them.
8. **The ledger is a table in the platform database recording each script's kind, content digest and
   application time; each script declares its kind itself.** A migration whose digest differs from its
   record is refused, a changed definition is re-applied, and a database without a ledger applies every
   script once, which dec 2 makes safe. One run at a time; a script without a declared kind is refused.
9. **The runner connects as the database owner, as a network client under ADR-0017.** Creating databases,
   roles and extensions needs the owner; it is the only workload holding every role's password, and the
   database server holds none.
10. **A database client starts only once the ledger is current.** Where start order holds, it starts
    behind the completed runner; where it does not, a read-only check of the ledger as the client's own
    role holds it back. A client of a database the ledger does not cover waits for its own login, and the
    backup, the step taken before an upgrade, waits for nothing.
11. **On an upgrade the stream engines are stopped before the pending scripts run and start behind the
    completed runner, and a migration corrects only data whose writers are stopped.** They write derived
    data continuously, so a correction they outlive is lost for good; stopping them delays aggregates
    and loses nothing, since delivery is at-least-once and their writes idempotent. The deployment
    guarantees the order, not the script.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Generate the schema from application models | Several services own the schema, extension objects sit outside any model layer, tenant schemas are created at run time |
| 4 | Operators apply new scripts by hand | Self-hosters cannot know which scripts their database has had |
| 5 | Down-migrations for every change | Lossy or untested once data is written through the change |
| 6 | State written by the last versioned release | The baseline lags the trunk by every unreleased merge, so a change fails on an earlier one's break; that upgrade is the box run's |
| 7 | A framework with versioned and repeatable scripts | Shell scripts are outside its free edition, secrets would become placeholders, and it adds a runtime and a vendor licence |
| 7 | A framework built on deploy, revert and verify scripts | Presumes the down-scripts dec 5 rejects |
| 7 | A framework without re-applied scripts | Cannot express a definition |
| 7 | The init hook for fresh installs, the runner recording afterwards | Two appliers; the fresh path would never exercise the runner |
| 8 | The kind of each script in a separate list | A list whose omission fails silently |
| 8 | The ledger outside the platform database | A second durable store to back up and offboard, which can disagree with the database it describes |
| 8 | Every definition re-applied on every run | Backfills over every tenant on every deploy |
| 9 | A dedicated migration principal below the owner | Cannot create the extension, the databases or the roles the sequence creates |
| 10 | Each service checking the ledger in its own code | Every service would carry the migration contract |
| 11 | The correction re-applied after the rollout, by a later hook or as a definition | The corrected data is wrong until it runs, every correction needs its own wiring, and a definition re-applied each run is dec 8's rejected backfill |
| 11 | Accept the gap and document it | Windows the old writer closes in it stay wrong for good; a statistic missing from a window that exists is a defect (ADR-0044 dec 3) |
| 11 | Each migration declaring the writers it depends on | A list whose omission fails silently |
| 11 | A writer refusing to write while the ledger is ahead of its build | Cannot protect an upgrade from the versions already running, which lack it |
| 11 | Every database client stopped for the sequence | Downtime for the stateless tier, whose additive migrations tolerate the old version |

## Consequences

**Positive**
- A deployment knows which schema it is on; an upgrade does not depend on memory.
- The silent per-tenant omission becomes a build failure.
- A data correction cannot be undone by the writer it corrects.

**Negative**
- Backfills over many tenants lengthen the upgrade.
- An optional role enabled after its script was applied needs that definition re-applied by hand.
- The ledger and upgrade step add moving parts to start-up.
- A bad migration costs a restore and the data written since the backup.
- Aggregates pause for every upgrade, not only one with a correction.
- On Kubernetes the upgrade needs a workload allowed to stop and start the stream engines.

## Deferred

| Open question | Settled by |
|---|---|
| Online and batched backfills for large tenants, and their locking | First tenant large enough to need it |
| Database major-version upgrades (dump/restore, not a script) | Maintainer, with ADR-0026 |
| Extension-owned tables and their migrations inside tenant schemas | ADR-0008, ADR-0010 |
| A correction of data a rolled service writes | The first such migration |

## References

- [init-scripts README](../infrastructure/timescaledb/init-scripts/README.md): manual of decisions 1-11.
