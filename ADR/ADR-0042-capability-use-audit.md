<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0042 — Capability use is recorded, by the planes that resolve it, into one durable record

- **Status:** Accepted
- **Date:** 2026-10-29; changed 2026-10-30
- **Parent:** [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (the capability and its
  store)
- **Related:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (dec 2: the
  kernel is untrusted), [ADR-0012](ADR-0012-notebook-credential-delivery.md) (dec 1: per-session
  credentials), [ADR-0030](ADR-0030-externally-authored-model-admission.md) (dec 3: the upload audience),
  [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (dec 1: the capability store is derivable),
  [ADR-0033](ADR-0033-tenant-lifecycle.md) (dec 6: the lifecycle record that outlives a tenant),
  [ADR-0016](ADR-0016-observability-and-monitoring-integration.md) (no log backend is shipped)

## Context

Untrusted kernel code reaches tenant data only through capabilities: opaque handles bound to one user,
one tenant and one audience, minted into a capability store and resolved by each plane on every request.
Nothing records that a handle was minted, used, refused or revoked, so who reached which tenant's data
through which plane cannot be answered after the fact. The planes that resolve handles hold the
capability store and no database principal; the store itself is derivable and not backed up. Logs are
plain text, kept briefly, and not shipped at all by the Kubernetes packaging.

## Decision

1. **A capability's life is recorded: its mint, its first use by each plane, its refusals (decision 8),
   its revocation or consumption, and the start and stop of the environment it was minted for.** Each record
   names the user, the tenant, the audience, the plane and the time. A SQL session's opening and closing
   are recorded as decision 8 bounds them; statements are not, because the proxy relays them unread.
2. **A handle appears in the record only as a non-secret identifier bound into its entry at mint.** The
   handle is a bearer secret; a reader of the record must not be able to use it. A presented handle with
   no entry is recorded by a short digest, enough to correlate repeated refusals and too short to search
   the handle space.
3. **A plane appends its event to a stream in the capability store, in the same atomic step as the store
   change the event describes.** A mint or revocation and its record therefore happen together or not at
   all, and the planes gain no database principal.
4. **One trusted writer drains the stream into an append-only table outside every tenant schema, through
   a privileged function.** It is written by nothing else and changed by no runtime role; the record is
   backed up with the database and outlives the tenant, like the lifecycle record (ADR-0033 dec 6).
5. **First use is recorded once per handle and plane; later uses are not.** Per-request records would
   tie every data read to a write; the first use already establishes which tenant a handle reached and
   through which plane.
6. **The record is read by a platform superuser for every tenant and by a tenant's administrator for
   their own.** The tenant filter is the caller's verified tenant (ADR-0003 dec 1).
7. **A stream the writer has not drained is bounded and its lag is observable.** Losing the capability
   store loses only what was not yet drained, and the lag alert names that window.
8. **An event a real entry can repeat at will, a refusal or a SQL session opening or closing, is
   recorded once per entry, plane and kind in a window, and the next record carries how many were not;
   a presented value naming no entry, once per plane and value in a window.** A kernel can repeat the
   first with its own handles at request rate, and the count, not each row, is the evidence; anyone
   can present the second, and a repeat says nothing the first did not.
9. **A plane records at most a bounded number of unknown values per source address in a window;
   requests themselves are not throttled.** Distinct random values are the one flood the per-value
   window does not bound, and they arrive past the edge as well as through it. A request's source is
   the client address the edge reports when the edge relayed it, and the connecting peer otherwise; a
   reported address is believed from the edge alone.
10. **The record keeps rows for a configured period, never less than thirty days, and its writer
    expires the rest.** The floor is the database's, whoever calls it, and outlasts the suspension that
    precedes a tenant's deletion (ADR-0033 dec 7).

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Recording every request | A write per data read, and no fact beyond first use |
| 1 | Recording SQL statements | The proxy relays them unread; a statement audit is the database's, with its own volume and data classification |
| 2 | The raw handle in the record | The record becomes a store of live credentials |
| 2 | A full hash of the handle at every plane | A handle-derived value in the record for every event, where a minted identifier carries none |
| 3 | Each plane writing to the database directly | A database principal for every plane that resolves untrusted handles |
| 3 | Log lines as the record | Retention drops them, the Kubernetes packaging ships no backend, and they cannot be queried once a tenant is gone |
| 3 | Recording after the store change, as a separate step | A crash between the two leaves a mint or revocation without its record |
| 4 | Recording in the capability store only | The store is derivable and not backed up |
| 4 | Deleting a tenant's records with the tenant | The record is evidence of what reached the tenant's data |
| 5 | Aggregating uses per time window | Buffered counts a crash loses, for no fact the first use lacks |
| 6 | Only the platform operator reads it | A tenant cannot answer who reached its own data |
| 8 | Recording every refusal of an unknown value | One row per request anyone can send: the record and the database grow at an attacker's rate |
| 8 | Counting unknown-value refusals only | A counter names no value and no plane to correlate |
| 8 | Recording a real entry's refusals and SQL sessions every time | One handle repeats them at request rate: the record grows at a kernel's rate, and a stream filling faster than it drains loses other tenants' undrained events to its bound |
| 8 | Recording only the first in a window, with no count | How often a handle was refused or reconnected is what distinguishes misuse from a mistake |
| 8 | A larger stream bound | Moves the point at which the flood displaces other events; bounds nothing |
| 9 | A request rate limit at the public edge | Kernels reach the planes inside the network, and it throttles use where only the record needed bounding |
| 9 | Relying on the per-value window alone | Every random value is new to it |
| 9 | The connecting peer as the source behind the edge | Every external client is the edge, so one client spends the budget for all of them |
| 9 | A reported client address from any peer | A kernel reaches the planes directly and could name a new source with every request |
| 10 | No retention | The record grows without bound in the platform database |
| 10 | Expiry by a separate operator job | A scheduler no deployment otherwise needs; the database's floor already bounds what any caller removes |
| 10 | A retention period per tenant | A tenant would shorten the evidence of what reached its own data |

## Consequences

**Positive**
- Who reached which tenant's data, through which plane and with which capability, is answerable.
- No plane gains a database credential, and no bearer secret enters the record.

**Negative**
- Every capability mint, revocation and first use writes one more entry to the capability store.
- Repeats of an unknown value within a window, and unknown values beyond a source's limit, are not in
  the record.
- A real entry's repeated events within a window appear only as a count on its next record, which is
  never written if the entry repeats nothing after the window.
- A compromised writer can remove rows older than the floor.
- Events not yet drained are lost with the capability store.
- Every plane that resolves handles carries the recording rule, as it carries the resolution rule.

## Deferred

| Open question | Settled by |
|---|---|
| Which code a kernel ran, and notebook versioning | Maintainer, when a record of executed code is required |
| A SQL statement audit | A deployment whose compliance requires one |
| Each external client its own source on Kubernetes, where the edge's pods share the network kernels run on | The first Kubernetes deployment of the platform |

## References

- `infrastructure/timescaledb/init-scripts/31-capability-audit.sql`: the table and its writer (dec 4, 6).
- `libs/industryflow_contracts/industryflow_contracts/capability_audit.py`: the event contract (dec 1–3, 5).
- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): the planes and their capabilities.
