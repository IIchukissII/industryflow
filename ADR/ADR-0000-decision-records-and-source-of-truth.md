<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0000 — Decision records and the single source of truth

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-17
- **Parent:** none (governs the form of every other ADR)

## Context

The platform's reasoning spans service code, deployment descriptions, database scripts, configuration
templates and documentation. Without a record of *why* a thing is the way it is, each contributor
re-derives the reason, and the guesses diverge. A fact held in more than one place drifts, and review
cannot tell which copy is right.

## Decision

1. **Architectural decisions are recorded as ADRs before they are propagated.** A decision that
   constrains downstream artifacts (service boundaries, tenancy, data flow, delivery semantics,
   authentication, storage, observability contracts) is written down before it reaches code. The ADR
   formalises the outcome of discussion; it never replaces it.
2. **ADRs own the why; artifacts own the what.** An ADR holds context, the decision, rejected
   alternatives and consequences. Concrete values (ports, topics, pool sizes, variable names, schemas,
   image tags) live in the artifact whose job is that value, and the ADR points at it.
3. **Every fact has exactly one authoritative home.** Another document that needs the value references
   it. Where artifacts cannot share a fact, a check reads every copy and fails when they disagree; where
   a copy is not needed, it is replaced by one shared definition.
4. **Code never silently overrides an ADR.** A divergence is resolved by changing the ADR deliberately
   or by changing the code.
5. **An ADR states the decision in force.** A decision that becomes untrue is edited in place to the
   current truth, with the change date in the header and the change described in its pull request.
   History lives in version control, not in the record. A reversed or replaced decision is a new ADR,
   and the old one is marked superseded.
6. **The governance root is not mirrored into the ADRs it governs.** No ADR names this one as its
   parent; the relationship is inherited.
7. **Status follows a lifecycle, and the maintainer accepts.** `Proposed` while under discussion,
   `Accepted` once the maintainer judges it binding, `Superseded` when replaced. Acceptance means the
   decision is settled, not that it is built; acceptance is the merge that sets the status.
8. **ADRs are numbered and named uniformly.** Files are `ADR-NNNN-kebab-title.md`, numbered in
   order. ADR-0000 is this root, ADR-0001 the project framing, technical decisions from 0002. A number
   is never reused; a decision number within an ADR is never renumbered, because code and documents
   cite it.
9. **Every ADR has the same compact shape.** Context, Decision (numbered), Alternatives (table),
   Consequences (positive, negative), Deferred (table), References. One decision per record, in about
   a hundred lines; a record that needs more holds several decisions.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Rationale in the README and docs only | Prose that mirrors values without owning them is a duplication source, not a decision record |
| Several copies with one marked primary | Markers rot; a reader cannot tell a stale copy from a current one |
| Decisions in code comments and commit messages | A cross-cutting decision has no single home; commits are not a body of current decisions |
| Revision blocks and dated addenda inside each ADR | The record grows into its own history, and the decision in force is hard to find |

## Consequences

**Positive**
- One place to read a decision's reason and one place to change a value; "is this duplicated?" is a
  mechanical review question.
- A reader sees the decision in force without reading its history.

**Negative**
- Contributors must keep the why/what split and follow a reference instead of reading a copy.
- Earlier wording of a decision is found in version control, not in the record.

## Deferred

| Open question | Settled by |
|---|---|
| A checker that every cross-reference resolves | First broken reference that reaches a reader |
| A map of which artifact owns which class of fact | The maintainer, when contributors repeatedly ask where a value lives |

## References

- [GLOSSARY.md](GLOSSARY.md): one definition per cross-cutting term.
- M. Nygard, "Documenting Architecture Decisions" (2011): the practice this record formalises.
- MADR (Markdown Any Decision Records): the section lineage.
- `tests/config_guards/`: the checks that hold unavoidable copies equal (decision 3).
