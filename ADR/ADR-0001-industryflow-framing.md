<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0001 — IndustryFlow framing: an open-core, general-purpose industrial-IoT platform

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-17
- **Related:** [ADR-0008](ADR-0008-extension-and-plugin-interface.md), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extensions)

## Context

The technical ADRs assume external adopters, many tenants and third-party device fleets. The platform
must be openly adoptable without a closed competitor running it as a service, must fund its own
development, and must stay generic across industrial domains.

## Decision

1. **IndustryFlow is an open-core, general-purpose industrial-IoT platform.** Its scope is the
   domain-generic substrate: multi-tenant ingestion, time-series storage, stream processing,
   threshold and ML-based alerting, model training and inference, and observability.
2. **Code is AGPL-3.0-or-later; documentation is CC-BY-SA-4.0.** Every file declares its licence in a
   machine-readable header, or, where its format cannot carry one, in a repository manifest.
3. **The commercial offering is dual-licensing, not feature-gating.** The whole repository is open; an
   organisation integrating IndustryFlow into a closed product buys an exception to the AGPL.
4. **Self-hosted and commercial-managed deployments run one architecture.** Neither is a fork or a
   reduced version of the other.
5. **Development is funded by the managed offering and by licence exceptions.**
6. **The first deployment is a downstream extension, which records its domain decisions itself.**
7. **Domain specifics live in extensions (ADR-0008, ADR-0010), not in the core.**

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | A fully proprietary, closed platform | Forecloses community adoption and contradicts the open-core intent |
| 2 | The permissive MIT licence the repository first carried | A closed competitor can run the platform as a service without contributing back |
| 3 | Open core with paid, feature-gated closed add-ons | Two codebases and two products to maintain, and an open/closed split in one repository |
| 4 | Self-hosted only, with no managed offering | Leaves the multi-tenant design unused for funding development |
| 7 | The platform as one domain application's backend | Domain specifics would enter the core, ending its reuse across domains |

## Consequences

**Positive**
- The core is shielded from closed service operation, open to self-hosting, reusable across domains.

**Negative**
- The AGPL deters permissive-only adopters, relicensing is one-directional, and selling exceptions
  needs a contributor agreement the project does not yet have.
- Keeping domain concerns out of the core needs continuous review.

## Deferred

| Open question | Settled by |
|---|---|
| Contributor agreement (CLA or copyright assignment) | The maintainer, before the first exception is granted |
| Commercial packaging, pricing, trademark policy | The maintainer |

## References

- `LICENSE`, `LICENSES/`, `REUSE.toml`: licence texts and the header manifest (decision 2).
