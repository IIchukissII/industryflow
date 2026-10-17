<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0034 — Platform schema migrations: one ordered, idempotent sequence for fresh installs and live tenants

- **ID:** ADR-0034
- **Status:** Proposed
- **Date:** 2026-10-13
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant — why a change is applied once per tenant), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (the upgrade-path gate), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (restore as the rollback), [ADR-0033](ADR-0033-tenant-lifecycle.md) (provisioning, the other half of every per-tenant change)
- **Related:** [ADR-0008](ADR-0008-extension-and-plugin-interface.md) / [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extension-owned tables, whose migrations are still deferred)

## Context and problem

The platform's database schema has evolved through roughly twenty numbered scripts, and the way they
are written has converged on a practice nobody decided.

The scripts live in one ordered sequence. The database image runs them, in order, when it starts on an
**empty** data volume, and the Kubernetes chart mounts the same sequence so both deployment shapes
initialise identically. The early scripts create the shared schema, the service roles and the function
that provisions a tenant. Every later one is, in effect, a migration, and they share a shape:

- a function that applies the change to **one** tenant schema, written so that applying it twice is
  harmless;
- a block that runs that function over **every existing** tenant schema;
- a change to tenant provisioning, so a tenant created **after** the script also has it. In practice
  this edits the early script that defines the provisioning function, so that function's definition
  changes long after the script that defines it was first run.

That is a good shape. Under schema-per-tenant (ADR-0003) a change is not applied once but once per
tenant, and the three parts are exactly what makes old tenants, new tenants and fresh installs agree.
But nothing records it, and each part is a place to forget: a script whose backfill is missing leaves
existing tenants behind; one that is not wired into provisioning leaves new tenants behind. Both fail
silently, and only for some tenants. One part is now guarded: a static check in the build fails when a
per-tenant shape function is not called from tenant provisioning. The backfill over existing tenants is
not checked, and neither is a per-tenant change written in any other form.

The other half is undecided outright. The image runs the sequence only against an empty volume, so on a
**live** database nothing applies a new script: an operator runs it by hand, and nothing records which
scripts a database has had. Upgrading a deployment therefore depends on someone remembering — including remembering to re-run the
early script that redefines provisioning, without which existing tenants get the change and new ones do
not. The
release gate's upgrade path (ADR-0026 dec 4) does not cover the database, which that record deferred.

## Decision drivers

- **Fresh installs and upgrades must reach the same schema.** Two paths to "current" are two schemas.
- **Schema-per-tenant multiplies every change by the tenant count**, and the tenants that exist at
  upgrade time are not the tenants that will exist later.
- **A step that must be remembered is not a step** (ADR-0026's driver, applied to the database).
- **An omission here fails silently and partially** — the repository's most expensive class of bug.
- **Rolling a schema back is rarely safe** once data has been written in the new shape.

## Decision

1. **One ordered sequence of numbered scripts is the only definition of the platform schema.** A fresh
   install and an upgraded database both get there by running the same sequence; there is no separate
   "current schema" file for new installs. The sequence holds two kinds of script, and the difference is
   recorded with each: a **migration** changes state, runs once per database, and is never edited after
   release — a further change is a further migration; a **definition** (the provisioning function and the
   functions it calls) describes behaviour, is written to be replaced in place, and is re-applied whenever
   it has changed.

2. **Every script is idempotent.** Run against a database that lacks the change, it converges; run
   against one that has it, it is a no-op. This is what makes the sequence safe to re-run and the
   upgrade step (decision 4) safe to retry.

3. **A per-tenant change has three parts, and all three are required:** the change for one tenant
   schema; its application to every existing tenant; and its inclusion in tenant provisioning
   (ADR-0033). A check that reads the scripts and the provisioning function fails the build when a
   per-tenant change lacks one of them.

4. **A live database is upgraded by applying the pending scripts in order, recorded in a ledger, before
   services start.** The database records which scripts it has applied. An upgrade step runs every
   migration not yet recorded, in order, re-applies every definition whose content has changed since it
   was recorded, and records each; services do not start against a database the ledger says is behind. The empty-volume initialisation remains how a fresh install runs the same
   sequence, and it writes the same ledger.

5. **Migrations are forward-only.** There are no down-scripts; the way back from a bad migration is a
   restore of the backup taken before the upgrade (ADR-0032 dec 7), which is why the upgrade step is
   preceded by one.

6. **The upgrade path is tested.** The release gate starts the database from state written by the
   previous release and applies the pending sequence, so a migration that cannot run against real prior
   state fails the merge (ADR-0026 dec 4; that record left open which stateful components earn an upgrade path, and this
   decision settles it for the database).

## Alternatives considered

**A. Keep the practice as it is, with operators applying new scripts by hand.** *Rejected:* it works on
one deployment run by the people who wrote the scripts. It does not scale to self-hosters, who cannot
know which scripts their database has had, and it is exactly the step-a-human-must-remember that
ADR-0026 removed from the release.

**B. Adopt a general-purpose migration framework and rewrite the sequence in it.** *Not chosen yet:* a
framework brings a ledger and an ordered runner, which decision 4 needs, and may be how decision 4 is
built. It does not bring the per-tenant backfill-and-provision shape, which is the part specific to
this platform; and rewriting twenty scripts that already converge buys risk for no schema change. The
choice of runner is deferred.

**C. Generate the schema from application models and diff it.** *Rejected:* several services own parts
of the schema, the time-series extension's objects (hypertables, compression, retention) are outside
any model layer, and per-tenant schemas are created at run time. A diff tool sees none of that.

**D. Down-migrations for every change.** *Rejected:* a down-script for a change that has had data written
through it is either lossy or untested, usually both. A restore is honest about what it returns to.

## Consequences

### Positive

- A deployment knows which schema it is on, and an upgrade does not depend on memory.
- The silent, per-tenant omission class becomes a build failure.
- Self-hosters get the same upgrade path the project's own deployment uses.

### Negative

- Backfills over many tenants run at upgrade time and lengthen it; large tenants may need the change
  applied in batches, which the idempotent shape permits but does not provide.
- The ledger and the upgrade step are new moving parts on the start-up path.
- Forward-only means a bad migration costs a restore and the data written since the backup.

## Deferred decisions

- **The runner and ledger format** — purpose-built or a framework (alternative B).
- **Online and batched backfills** for large tenants, and the locking they may need.
- **Database major-version upgrades**, which are a dump/restore problem rather than a script (ADR-0026
  deferred them too).
- **Extension-owned tables and their migrations** inside tenant schemas (ADR-0008, ADR-0010).

## References

- `infrastructure/timescaledb/init-scripts/README.md` — the current sequence and how to apply a script
  to a live database by hand until decision 4 exists.
- ADR-0026 dec 4 — the upgrade path the release gate exercises.
- ADR-0033 — provisioning, the third part of every per-tenant change.
