<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0000: Decision records and the single-source-of-truth discipline (rev 1)

- **ID:** ADR-0000
- **Status:** Accepted
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** — (root; this ADR governs the form of all other ADRs)
- **Supersedes:** ADR-0000 (rev 0) — extends decision 5 to permit labeled status addenda on Accepted ADRs

## Context and problem

IndustryFlow's reasoning is spread across many artifacts: service code in seven deployment units (api_gateway, ml_service, alert_service, ingestion_service, mock_service, spark_jobs, frontend), a `docker-compose.yml`, SQL init scripts, `.env.example`, the `docs/` architecture and API write-ups, and the README. Today none of these records *why* a thing is the way it is — only *what* it is, and often not even that consistently. The same decision is re-expressed independently in several places, and the copies have drifted: the README documents an `admin/admin` login that no longer exists, advertises MLflow on port 5001 while compose binds 5000, claims a Spark cluster processes "millions of readings per second" while the jobs run `--master local[*]`, and lists benchmarks no code substantiates. The schema-per-tenant resolution logic is copy-pasted across four services, each copy carrying the same flaw. There is no place a contributor can read the reasoning behind multi-tenancy, the Kafka delivery contract, or the auth model — so each is re-derived, slightly differently, every time it is touched.

An unrecorded rationale has two failure modes. A contributor cannot recover the *why*, so they reimplement it from a guess and the guesses diverge. And there is no authoritative home for a fact, so "where do I change the Kafka topic name / the tenant-schema rule / the CORS policy?" has several answers and none is correct. Both failures are the same underlying problem: a fact or a decision that lives in more than one place has more than one place to go wrong, and the review of "is this duplicated?" cannot be made mechanical.

This ADR establishes the discipline the project currently lacks and makes it the explicit root of the decision record. It is deliberately the lowest-numbered ADR because it governs the form of every other ADR rather than any technical subsystem. The practice is adopted from the sibling project IndustryGrow, which is built on IndustryFlow and already runs its architecture this way; IndustryFlow is the underlying platform and should hold its own decisions to the same standard rather than inheriting them second-hand.

**Revision 1.** Practice since acceptance surfaced a gap in decision 5. Several Accepted ADRs (e.g. ADR-0009 and ADR-0019) grew short, clearly-labeled sections recording what was *resolved or implemented* after acceptance — outcomes and resolved deferrals, not new decisions — which decision 5 as written sanctioned only on a still-Proposed draft. The converse also occurred: ADR-0010 grew an after-the-fact *anomaly-detector contract*, which is a new decision rather than a status note, and was therefore taken to a revision (ADR-0010 rev 1) rather than left as an addendum. Forcing full supersession ceremony onto every post-acceptance status note discourages the very act of keeping the record current, while leaving the practice unaddressed lets a genuine new decision blur into a silent in-place edit. This revision draws the line explicitly: a labeled status addendum that changes no recorded decision is allowed on an Accepted ADR; anything that adds or alters a *decision* still takes a revision.

## Decision drivers

- **Rationale decays slowest when captured once.** The *why* behind a decision is the part hardest to reconstruct later and easiest to lose; it needs exactly one durable home.
- **Duplication is the dominant source of documentation entropy.** N copies of a fact are N independent opportunities to drift; the only copy count that cannot drift is one. The README/compose/`.env.example` divergences already on record are this failure in the live codebase.
- **A contributor needs one place to look and one place to change.** If a value can be edited in two documents, review cannot be mechanical and drift is caught by memory rather than by process.
- **Copy-pasted decisions propagate copy-pasted bugs.** The tenant-resolution logic duplicated across services shows that an unrecorded *decision* drifts the same way an unrecorded *value* does — and carries its defects with it.
- **The platform should own its decisions directly.** IndustryGrow records why IndustryFlow is used; IndustryFlow must record why IndustryFlow is *built the way it is*, in its own repository, as its own source of truth.

## Decision

1. **Architectural and design decisions are recorded as ADRs.** A decision that constrains downstream artifacts — service boundaries, multi-tenancy, data flow, message-delivery semantics, authentication, storage and schema design, observability contracts — is captured in an ADR before it is propagated into code, compose files, or SQL. Discussion precedes the ADR; the ADR formalizes the outcome, never the reverse. ADRs hold decisions and rationale, not implementation: an ADR states *that* tenant data is isolated per schema and *why*; the SQL init scripts own the live schema definitions.

2. **ADRs own the *why*; downstream artifacts own the *what*.** An ADR holds context, decision drivers, the decision, rejected alternatives, and consequences. Concrete values — port numbers, topic names, connection-pool sizes, environment-variable names, table and column definitions, image tags — live in the artifact whose job is that value: `docker-compose.yml`, `.env.example`, the SQL init scripts, the service config modules. An ADR records *that* the ingestion topic exists and *why* the pipeline is shaped around it; `.env.example` owns its literal name.

3. **Single source of truth: every fact has exactly one authoritative home.** No value is mirrored into a second document. Where another document needs a value, it references the authoritative source rather than copying it. The cheapest invariant against drift is zero copies, so that is the invariant. The README's port and credential claims are to be cut down to references, not maintained as a parallel copy.

4. **Downstream artifacts must never silently override an ADR decision.** If a service or config needs to diverge from a recorded decision, the divergence is resolved by amending the ADR, not by quietly changing the code. A silent override — the README claiming a deployment shape the code does not implement — is the canonical anti-pattern this discipline exists to forbid.

5. **A revision is a supersession, not a silent edit.** A substantive change to a decision already on record produces a new revision: the title gains `(rev N)`, the metadata gains a `Supersedes:` line, and the reason is woven into *Context and problem* and *Alternatives considered*. A clarifying addition that changes no existing decision may be made in place on a still-Proposed draft without a revision bump. On an **Accepted** ADR, a clearly-labeled status addendum — a *Resolved since acceptance* or *Added after acceptance* note that records an outcome, resolves a previously deferred item, or reports what implementation settled — may likewise be added in place, **provided it changes no decision already recorded**; introducing a new decision or altering an existing one still requires a revision (or a new ADR).

6. **The governance root is not mirrored into the ADRs it governs.** This ADR applies to every ADR by being the root; individual ADRs do not back-reference it in their metadata. Enumerating "governed by ADR-0000" in each ADR would be exactly the mirroring this ADR forbids. The relationship is inherited, not copied.

7. **Status lifecycle and accepting authority.** An ADR is `Proposed` while under discussion, `Accepted` once the maintainer judges the decision binding, and `Superseded` when a later revision or ADR replaces it (per decision 5). Acceptance records *agreement that the decision is settled*, not that it is implemented — a decision may be Accepted while the code still carries the old behaviour, and the gap between them is tracked as ordinary work, not hidden by withholding acceptance. The accepting authority is the project maintainer; acceptance is effected through the normal review-and-merge process by setting the `Status` field.

8. **ADRs are numbered and named uniformly.** Files are `ADR/ADR-NNNN-kebab-case-title.md`, allocated in increasing order. ADR-0000 is this governance root; ADR-0001 is reserved for project framing (scope, licensing, audience); technical decisions take numbers from 0002 upward. A number, once allocated, is never reused.

## Alternatives considered

**A. Leave the rationale unwritten and rely on the README and `docs/`.** *Rejected:* this is the status quo, and the status quo is precisely what produced the README/compose/`.env.example` drift this ADR cites. Prose documentation that mirrors values without owning them is a duplication source, not a decision record.

**B. Allow controlled duplication with a "primary copy" marker.** Permit a value in several documents, one marked authoritative. *Rejected:* markers rot, copies drift between syncs, and a reader cannot tell a stale copy from a current one without re-checking the marker every time. Zero copies is cheaper to guarantee than one-authoritative-among-many.

**C. Keep decisions in code comments and commit messages.** *Rejected:* comments are scoped to one file and one service, so a cross-cutting decision (multi-tenancy, delivery semantics) has no single home and gets re-explained per service — the copy-paste-the-decision failure already in the codebase. Commit messages are not discoverable as a body of current decisions.

**D. Inherit IndustryGrow's ADRs by reference instead of keeping our own.** *Rejected:* IndustryGrow's ADRs decide IndustryGrow's concerns and treat IndustryFlow as a given. IndustryFlow's internal decisions (schema-per-tenant, Kafka→Spark→TimescaleDB, the auth model) are not recorded there and must not depend on another project's repository for their source of truth.

**E. Require a revision bump for any post-acceptance edit, including implementation-status notes *(rev 1)*.** *Rejected:* it would attach full supersession ceremony (rev title, `Supersedes:`, narrative rationale) to merely recording that a deferred item was resolved or that code settled an implementation detail — friction heavy enough to discourage keeping the record current, which is the opposite of this ADR's aim. Decision 5 instead admits labeled status addenda that change no decision, and reserves revisions for changes to the decisions themselves.

## Consequences

### Positive

- There is one place to look for a decision's rationale and one place to change a value; the review question "is this duplicated?" becomes mechanical.
- Cross-cutting decisions get a single authoritative statement, so they can no longer be copy-pasted-with-drift across services the way the tenant-resolution logic was.
- The README and `docs/` can be reduced to references and current *what*, ending the drift between documented and actual behaviour.
- Rationale survives changes of contributors, tooling, and memory, because it is captured once in a durable artifact in the platform's own repository.

### Negative

- Every substantive change to a recorded decision now carries supersession ceremony (rev title, `Supersedes:`, narrative rationale) rather than a quick edit. This is intentional friction — the cost of a non-drifting record.
- Contributors must learn the *why/what* split and resist the convenience of copying a value into the document they happen to be editing.
- Cross-references replace inline copies, adding a layer of indirection: obtaining a value sometimes means following a reference to its authoritative home rather than reading it where it is used.
- Retrofitting the discipline onto an existing codebase means an initial backlog of decisions to capture (multi-tenancy, ingestion authentication, delivery semantics, API auth, configuration) before the record is complete.

## Deferred decisions

- **ADR-0001 framing.** The project's scope, licensing posture, intended audience, and relationship to IndustryGrow are a framing decision to be recorded in ADR-0001, not here. (ADR-0001 has since been accepted and relicensed the project to AGPL-3.0-or-later; this ADR does not restate the licence to avoid the very duplication it forbids.)
- **ADR template.** A skeleton enforcing the section structure shared across ADRs is implied but not yet written.
- **Cross-reference and duplication tooling.** A linter that flags a value duplicated across README / compose / `.env.example`, or a checker that validates references resolve, is desirable but unspecified.
- **The authoritative-home registry.** A concise map of which artifact owns which class of fact (ports/topics → `.env.example`, schema → SQL init scripts, rationale → ADR) could be maintained, but its location and format are open.

## References

- [`GLOSSARY.md`](GLOSSARY.md) — the single authoritative definition of each cross-cutting term, applying this ADR's single-source discipline to vocabulary.
- M. Nygard, "Documenting Architecture Decisions" (2011) — the ADR practice this record formalizes for the project.
- MADR (Markdown Any Decision Records) — format lineage for the ADR structure in use.
- IndustryGrow ADR-0000 — the sibling project's governance root, from which this discipline is adopted.
