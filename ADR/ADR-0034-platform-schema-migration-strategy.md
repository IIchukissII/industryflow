<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0034 — Platform schema migrations: one ordered, idempotent sequence for fresh installs and live tenants

- **Status:** Accepted
- **Date:** 2026-10-24
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (a change applies once per tenant), [ADR-0008](ADR-0008-extension-and-plugin-interface.md), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extension-owned tables), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (dec 4: upgrade-path gate), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (dec 7: restore as rollback), [ADR-0033](ADR-0033-tenant-lifecycle.md) (provisioning)

## Context

Under schema-per-tenant a schema change applies to every existing tenant and to every tenant provisioned
later; missing either half fails silently, for some tenants only. The build checks only that per-tenant shape
functions are called from provisioning. A live database gets new scripts only by hand, and nothing records
which scripts it has had. Rolling back is rarely safe once data is written in the new shape.

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
   before the upgrade.
6. **The upgrade path is tested.** The release gate applies the pending sequence to state written by the
   previous release, settling the database under ADR-0026 dec 4.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Generate the schema from application models | Several services own the schema, extension objects sit outside any model layer, tenant schemas are created at run time |
| 4 | Operators apply new scripts by hand | Self-hosters cannot know which scripts their database has had |
| 5 | Down-migrations for every change | Lossy or untested once data is written through the change |

## Consequences

**Positive**
- A deployment knows which schema it is on; an upgrade does not depend on memory.
- The silent per-tenant omission becomes a build failure.

**Negative**
- Backfills over many tenants lengthen the upgrade.
- The ledger and upgrade step add moving parts to start-up.
- A bad migration costs a restore and the data written since the backup.

## Deferred

| Open question | Settled by |
|---|---|
| The runner and ledger format: purpose-built or a framework | Maintainer, when decision 4 is built |
| Online and batched backfills for large tenants, and their locking | First tenant large enough to need it |
| Database major-version upgrades (dump/restore, not a script) | Maintainer, with ADR-0026 |
| Extension-owned tables and their migrations inside tenant schemas | ADR-0008, ADR-0010 |

## References

- [init-scripts README](../infrastructure/timescaledb/init-scripts/README.md) (the sequence, manual apply); `infrastructure/timescaledb/tests/test_tenant_creation_completeness.py` (provisioning half of decision 3's check).
