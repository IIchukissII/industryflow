<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0034 — Platform schema migrations: one ordered, idempotent sequence for fresh installs and live tenants

- **Status:** Proposed
- **Date:** 2026-10-13; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant: a change applies once per tenant), [ADR-0008](ADR-0008-extension-and-plugin-interface.md) and [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extension-owned tables), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (the upgrade-path gate), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (restore as the rollback), [ADR-0033](ADR-0033-tenant-lifecycle.md) (provisioning)

## Context

The platform schema is defined by one ordered sequence of numbered scripts. The database image runs it
on an empty data volume, and both deployment shapes mount the same sequence. The early scripts create
the shared schema, the service roles and the tenant provisioning function; each later script is a
migration of a common shape: a function that applies the change to one tenant schema and is harmless
twice, a pass over every existing tenant, and a change to provisioning so later tenants get it too.

Under schema-per-tenant each part is a place to forget, and each omission fails silently for some
tenants only. The build checks that each per-tenant shape function is called from provisioning; it does
not check the backfill over existing tenants or a per-tenant change in any other form. On a live
database nothing applies a new script: an operator runs it by hand, including re-applying a changed
provisioning definition, and nothing records which scripts a database has had.

## Decision

1. **One ordered sequence of numbered scripts is the only definition of the platform schema.** Fresh
   installs and upgraded databases run the same sequence; there is no separate current-schema file. Each
   script records its kind: a **migration** changes state, runs once per database and is never edited
   after release; a **definition** (the provisioning function and what it calls) describes behaviour, is
   replaced in place and re-applied whenever it changes.
2. **Every script is idempotent.** Against a database that lacks the change it converges; against one
   that has it, it is a no-op. This makes the sequence safe to re-run and decision 4's step safe to retry.
3. **A per-tenant change has three required parts:** the change for one tenant schema, its application
   to every existing tenant, and its inclusion in tenant provisioning (ADR-0033). A check that reads the
   scripts and the provisioning function fails the build when a part is missing.
4. **A live database is upgraded by applying the pending scripts in order, recorded in a ledger, before
   services start.** The upgrade step runs every unrecorded migration in order, re-applies every
   definition whose content changed since it was recorded, and records each. Services do not start
   against a database the ledger shows behind. Fresh-install initialisation writes the same ledger.
5. **Migrations are forward-only.** There are no down-scripts; the way back is a restore of the backup
   taken before the upgrade (ADR-0032 dec 7), so one precedes every upgrade.
6. **The upgrade path is tested.** The release gate starts the database from state written by the
   previous release and applies the pending sequence, so a migration that cannot run against real prior
   state fails the merge. This settles the database under ADR-0026 dec 4, which leaves open which
   stateful components earn an upgrade path.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Operators apply new scripts by hand | Works only for the people who wrote the scripts; self-hosters cannot know what their database has had |
| Rewrite the sequence in a general-purpose migration framework | Not chosen yet: it brings a ledger and runner (decision 4) but not the per-tenant backfill-and-provision shape, and rewriting scripts that converge adds risk for no schema change |
| Generate the schema from application models and diff it | Several services own parts of the schema, time-series extension objects are outside any model layer, and tenant schemas are created at run time |
| Down-migrations for every change | A down-script for a change data has been written through is lossy or untested |

## Consequences

**Positive**
- A deployment knows which schema it is on; an upgrade does not depend on memory.
- The silent per-tenant omission becomes a build failure.
- Self-hosters get the same upgrade path as the project's own deployment.

**Negative**
- Backfills over many tenants lengthen the upgrade; large tenants may need batching, which idempotent
  scripts permit but do not provide.
- The ledger and upgrade step are new moving parts on the start-up path.
- A bad migration costs a restore and the data written since the backup.

## Deferred

| Open question | Settled by |
|---|---|
| The runner and ledger format: purpose-built or a framework | Maintainer, when decision 4 is built |
| Online and batched backfills for large tenants, and their locking | First tenant large enough to need it |
| Database major-version upgrades (a dump/restore problem, not a script) | Maintainer, with ADR-0026 |
| Extension-owned tables and their migrations inside tenant schemas | ADR-0008, ADR-0010 |

## References

- [infrastructure/timescaledb/init-scripts/README.md](../infrastructure/timescaledb/init-scripts/README.md):
  the sequence, and applying a script to a live database by hand.
- `infrastructure/timescaledb/tests/test_tenant_creation_completeness.py`: the provisioning half of
  decision 3's check.
