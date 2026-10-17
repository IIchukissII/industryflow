<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0008 — Extension interface for a domain-generic core

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** ADR-0010 (the plugin mechanism and versioning), ADR-0003 (the per-tenant schema),
  ADR-0028 (the model-adapter contract)

## Context

The core is domain-generic and domain specifics live in extensions (ADR-0001). Domain knowledge is
data, code or entities; each needs a place outside the core, and the platform must depend on no domain.

## Decision

1. **The core defines versioned extension contracts; extensions implement them and never patch the
   core.** A domain need is met by an existing or a new generic extension point.
2. **Configuration is the primary extension surface.** Feature definitions, sensor mappings, alert
   rules and operating profiles are declarative.
3. **Code plugins extend behaviour where configuration cannot.** Transforms, detectors and model
   adapters register through a declared interface.
4. **Domain data lives in extension-owned tables in the tenant schema, not as columns on core tables.**
5. **Extensions target a declared extension-API version.** The compatibility rule is ADR-0010 dec 3.
6. **Extensions are packaged separately and depend on the platform, which never imports them.** An
   in-repository reference extension, loaded only when configured, demonstrates the boundary.
7. **The boundary is enforced in review.** Anything domain-named defaults to an extension.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| — | Fork per domain | Every platform fix re-applied per fork |
| 1 | Domain features behind flags in the core | The core accretes every domain |
| 3 | Configuration only | Custom logic would have nowhere but the core |
| 3 | Every plugin a service | A network hop per feature; in-process is the default (ADR-0010 dec 4) |
| 4 | Domain columns on core tables | Couples the core schema to domains |
| 6 | A downstream project's extension as the only reference extension | The proof of the boundary would live outside the platform's own repository and tests |

## Consequences

**Positive**
- A new domain builds on the core without forking, at its own release pace.

**Negative**
- Versioned contracts are a standing compatibility commitment.
- In-process plugins run with the service's privileges.
- The boundary relies on review discipline and release coordination.

## Deferred

| Open question | Settled by |
|---|---|
| When a plugin must run out of process | First untrusted or non-Python plugin |
| Plugin trust model for the managed offering | First third-party plugin there |
| Extension-owned table registration and migration | First extension shipping tables |
| Packaging of domain dashboards and profiles | First extension shipping them |

## References

- [docs/operations/extensions.md](../docs/operations/extensions.md); `extensions/tep-reference/`.
