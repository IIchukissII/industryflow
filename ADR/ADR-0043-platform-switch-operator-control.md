<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0043 — A platform switch is flipped by a platform superuser through the API gateway, and every flip is recorded

- **Status:** Accepted
- **Date:** 2026-10-30
- **Parent:** [ADR-0024](ADR-0024-stateful-feature-kill-switch.md) (the switch, and the operator surface
  it deferred)
- **Related:** [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (dec 5: cross-tenant
  authority is a distinct superuser), [ADR-0031](ADR-0031-alerting-http-surface-single-owner.md) (the gateway as the
  one HTTP surface), [ADR-0033](ADR-0033-tenant-lifecycle.md) (dec 1: privileged writes through a
  function; dec 6: the lifecycle record), [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (platform
  data in the shared schema)

## Context

The stateful-feature switch is platform-global: one flip neutralizes a feature class for every tenant.
Only the database owner can change it, so an incident needs a person with database credentials, and
nothing records who flipped it or when beyond the last change. The service that reads it holds read
access only, so that it cannot trip itself. Cross-tenant authority already exists as one modelled
superuser, verified by the API gateway, which serves the other platform operations.

## Decision

1. **Only a platform superuser flips a platform switch; no tenant role can.** A flip acts on every
   tenant, and a tenant's authority ends at its own tenant (ADR-0004 dec 5).
2. **The API gateway serves the flip and the read of a switch's state and history.** It already
   verifies the session and the superuser; the inference service, which reads the switch, gains no
   write surface and no second verification.
3. **The gateway writes through one privileged database function and holds no write privilege on the
   switches.** The function accepts only a known switch and a value of its type, and records the flip
   in the same transaction.
4. **Every flip is appended to a durable record: the switch, the value before and after, the user and
   the time.** The current row keeps only the last change, and the questions an incident review asks
   are about the sequence.
5. **A flip that changes nothing is not recorded.** Repeating the current value is no action.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | A tenant administrator flips it | It would act on every other tenant |
| 1 | A separate platform-operator role | A second cross-tenant authority beside the superuser, granting nothing it lacks |
| 1 | Database-owner access only | An incident waits for a person with database credentials, and the change is unrecorded |
| 2 | The inference service serves the flip | A write surface and a second session verification on the service the switch protects |
| 2 | A dedicated administration service | A deployable, a database principal and a session path for one control |
| 3 | An update grant on the switch table to the gateway | Wider than the one change it needs, and the record would be a second, separable statement |
| 3 | The flip and its record as two statements from the gateway | A failure between them leaves a flip without its record |
| 4 | The last change on the switch row as the record | The next flip overwrites it |
| 4 | Log lines as the record | Retention drops them, and the Kubernetes packaging ships no log backend |
| 5 | Recording every request | Rows that record no change |

## Consequences

**Positive**
- An operator flips the switch with their platform session, during an incident, without database
  credentials.
- Who flipped which switch, when and from what to what is answerable.

**Negative**
- A stolen superuser session can neutralize a feature class for every tenant.
- A new switch needs its key and type added to the function's closed list.

## Deferred

| Open question | Settled by |
|---|---|
| None | — |

## References

- `infrastructure/timescaledb/init-scripts/17-stateful-feature-kill-switch.sql`: the switch.
- [docs/operations/monitoring.md](../docs/operations/monitoring.md): the runbook.
