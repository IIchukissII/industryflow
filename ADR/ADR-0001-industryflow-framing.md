<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0001: IndustryFlow framing — an open-core, general-purpose industrial-IoT platform

- **ID:** ADR-0001
- **Status:** Accepted
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** — (framing root; the technical ADRs take this as their Parent)

## Context and problem

IndustryFlow is a multi-tenant industrial-IoT platform: it ingests high-velocity sensor streams, processes them through Kafka and Spark into TimescaleDB, detects anomalies with ML models, raises alerts, and ships with a full observability stack. The technical architecture exists and is under active repair (the other ADRs in this directory record cross-cutting decisions surfaced by a code review). What has never been written down is the *shape of the project itself*: what IndustryFlow is, who it is for, how it is licensed, how it sustains itself, and where its boundary lies relative to the things built on top of it.

That gap is not cosmetic. Every technical ADR here already declares `Parent: ADR-0001`, because each one assumes answers to framing questions it does not itself decide. ADR-0004 assumes there are external adopters whose browsers must be served trusted certificates and whose tenants must be isolated; ADR-0002 assumes a fleet of third-party gateways with hardware identities; the whole schema-per-tenant design assumes more than one tenant and therefore more than one operator. Those assumptions only make sense under a particular framing — a general-purpose platform with real external users — and that framing was being applied by habit, never stated. This ADR states it, so the technical decisions have a real parent rather than an implied one.

There is also a concrete inconsistency to resolve. The repository is licensed **MIT** today, but the sibling project **IndustryGrow** — a cultivation platform built on IndustryFlow, by the same maintainer — is **AGPL-3.0 open-core**, and IndustryGrow's own framing (its ADR-0001) describes "the platform" as part of its copyleft open core. IndustryFlow and IndustryGrow therefore disagree about IndustryFlow's license. A framing ADR is the right place to settle which is correct, because the answer follows from what the project is meant to be, not from a file.

The decision is made now, rather than later, because licensing, audience, the open/commercial boundary, and the platform's scope all constrain downstream choices — repository structure, contribution policy, what may be accepted into the core, and how the managed and self-hosted deployments relate. Framing precedes implementation.

## Decision drivers

- **The platform must scale from self-hosted to managed.** The multi-tenant, schema-per-tenant design is already an investment in serving many operators; the framing should make use of it, not strand it.
- **Open adoption is strategically valuable, but the core must not be strip-mined.** Community self-hosting and a real external user base are the point; a permissive license that lets a closed competitor run the platform as a service without contributing back undermines the sustainability the project needs.
- **It must sustain its own development.** This is not a hobby in the long run; there has to be a funding path that does not depend on goodwill.
- **IndustryFlow needs a first real deployment to prove it.** It had none; IndustryGrow becomes the first end-to-end deployment and, by being first, shapes priorities — without IndustryFlow collapsing into "IndustryGrow's backend."
- **The platform must stay domain-generic.** Cultivation is one domain; others should be able to build on the same core. Domain specifics belong in extensions, not in the platform.
- **Framing precedes technology.** The technical ADRs already depend on this record; it should exist as their explicit parent.

## Decision

1. **IndustryFlow is an open-core, general-purpose industrial-IoT platform.** Its scope is the domain-generic substrate: multi-tenant ingestion, time-series storage, stream processing and aggregation, threshold and ML-based alerting, model training/inference, and observability. It is built to be adopted by anyone doing industrial IoT, not for a single domain.

2. **The platform is licensed AGPL-3.0-or-later; documentation is licensed CC-BY-SA-4.0.** The AGPL's network clause requires anyone offering the platform as a service to share their modifications, which protects the core against SaaS strip-mining. Documentation (these ADRs and the `docs/` tree) is CC-BY-SA-4.0. This supersedes the repository's prior MIT license; rolling the new license across the `LICENSE` file and the SPDX headers is a consequence of this decision (see Consequences).

3. **The commercial offering is pure dual-licensing: closed-integration exceptions, not feature-gating.** The entire repository stays open under decision 2 — nothing is held back as a proprietary "enterprise" add-on. An organization that needs to integrate IndustryFlow into a closed/proprietary product without accepting the AGPL's obligations buys a commercial license that grants an exception to the AGPL. The product is one open codebase, sold under two licenses, not an open core with a closed shell.

4. **Self-hosted and commercial-managed deployments coexist under one architecture.** The community may self-host the AGPL platform; a commercial managed/hosted offering runs the same platform as a service. The multi-tenant design serves both, and neither deployment is a forked or feature-reduced version of the other.

5. **Sustainability comes from the managed offering and commercial-license exceptions.** Continued development is funded by the managed service and by selling AGPL exceptions to organizations that need them — the model that makes "not a hobby in the long run" actionable without closing the source.

6. **IndustryGrow is the first deployment, not the definition.** IndustryGrow is built on IndustryFlow and is its first end-to-end real deployment; it proves the platform and informs its priorities. IndustryFlow remains general-purpose: IndustryGrow is the first consumer, not the only intended one. Per ADR-0000, IndustryFlow owns its platform decisions in this repository and IndustryGrow owns its cultivation-domain decisions in its own; neither mirrors the other's records.

7. **Domain specifics live in extensions, not in the core.** The platform stays domain-generic and exposes extension points; domain entities, profiles, and types (as IndustryGrow contributes for cultivation) are built on top of the platform rather than added into it. Keeping that boundary clean is what lets a second and third domain reuse the same core. The concrete extension/plugin interface is a technical decision deferred to its own ADR.

## Alternatives considered

**A. Keep the permissive MIT license.** *Rejected:* MIT lets a closed competitor operate the platform as a service without contributing changes back, which directly undermines decision 5's sustainability goal. AGPL is chosen specifically for its network-use copyleft; the protection against strip-mining is the reason, and MIT does not provide it. This also resolves the IndustryFlow/IndustryGrow license disagreement in favour of IndustryGrow's framing.

**B. Make IndustryFlow fully proprietary/closed.** *Rejected:* it forecloses community adoption and the "first real deployment proves the platform" strategy, and it contradicts the open-core intent. The value of an external user base and community contribution outweighs the control a closed model would give.

**C. Open core with proprietary, feature-gated enterprise add-ons.** *Rejected:* feature-gating splits the codebase into open and closed tiers, creates a two-product maintenance burden, and complicates the single-source-of-truth repository discipline (ADR-0000). Pure AGPL-plus-exception (decision 3) keeps the whole repository open and the model simple: one codebase, two licenses.

**D. Self-hosted only, with no managed offering.** *Rejected:* it leaves the multi-tenant investment unused for sustainability and forgoes the managed revenue that funds development. The architecture was built for many tenants; the framing should monetize that, not ignore it.

**E. Treat IndustryFlow as only IndustryGrow's backend.** *Rejected:* the platform is built domain-generic and other domains are an explicit goal; collapsing it into a single application's backend would justify baking cultivation specifics into the core (violating decision 7) and abandon the general-platform thesis. IndustryGrow is first, not sole.

## Consequences

### Positive

- The project has a stated identity, license, and funding model, and the technical ADRs now have a real parent rather than an implied one.
- The AGPL protects the core from being run as a closed service without reciprocity, while still letting the community self-host and contribute.
- The managed offering and commercial-license exceptions give a concrete, source-open path to funding continued development.
- A clean domain-generic boundary keeps the platform reusable across domains, with IndustryGrow as the proving deployment rather than a constraint.
- The IndustryFlow/IndustryGrow license inconsistency is resolved.

### Negative

- The license change from MIT to AGPL-3.0-or-later must be rolled out across the repository — the `LICENSE` file, and the SPDX headers in code (→ AGPL-3.0-or-later) and in the existing ADRs/docs that currently read `MIT` (→ CC-BY-SA-4.0). It also changes the obligations of anyone already relying on the MIT grant, and relicensing is effectively one-directional.
- AGPL deters some adopters who will only use permissively-licensed software; that lost adoption is an accepted cost of the strip-mining protection.
- Selling commercial exceptions requires the project to hold the rights to grant them, which means a contributor licensing agreement or copyright assignment — real governance the project does not yet have (deferred below).
- Running a managed offering and a licensing business is ongoing operational work beyond engineering.
- Keeping domain concerns out of the core (decision 7) demands continuous review discipline; the convenient path of adding a domain feature "just here" is exactly what erodes the boundary.

## Deferred decisions

- **Contributor licensing (CLA / copyright assignment).** Dual-licensing (decision 3) requires the rights to grant commercial exceptions; the contributor-agreement mechanism is necessary but unspecified here.
- **License rollout mechanics.** Which paths are AGPL (code) versus CC-BY-SA-4.0 (docs), and the actual edit of `LICENSE` and the SPDX headers, are an implementation task following acceptance.
- **Commercial packaging and pricing.** The shape of the managed offering and the terms of the commercial license are business decisions, not fixed here.
- **Extension/plugin interface.** How domains plug into the generic core (decision 7) is a technical ADR of its own.
- **Trademark and branding policy.** Whether and how the IndustryFlow name is protected, separate from the code license, is open.

## References

- IndustryGrow ADR-0001 — the sibling application's framing, which treats IndustryFlow as its platform and whose open-core/AGPL model this ADR aligns IndustryFlow with.
- ADR-0000 — decision records and single-source-of-truth; establishes that IndustryFlow owns its decisions in its own repository.
- IndustryFlow review (2026-06-26) — the code review whose cross-cutting findings prompted the ADR initiative this framing now roots.
