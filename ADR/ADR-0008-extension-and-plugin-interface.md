<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0008: Extension and plugin interface — keeping the core domain-generic

- **ID:** ADR-0008
- **Status:** Accepted
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0003 (tenant→schema resolution)
- **Refined by:** ADR-0010 (the in-process registry and feature-transform contract that realizes this interface)
- **Related (IndustryGrow):** ADR-0001 (the reference extension; contributes the production_unit entity, cultivation-domain types, and plugin interfaces)

## Context and problem

ADR-0001 decided that IndustryFlow is a domain-generic platform and that domain specifics live in extensions, not in the core — and it deferred the actual extension mechanism to its own ADR. That mechanism now needs deciding, because the boundary it describes is already being crossed informally. The ml_service carries a `feature_configs/tep_reactor_config.json` — a Tennessee-Eastman-Process-specific feature definition sitting inside the platform; the mock service ships a `sensors_mapping.json`; alert rules encode domain thresholds. These are domain knowledge living in platform code by convention, with no defined surface that says "this is where a domain plugs in and this is where the core ends."

Without a stated extension model, the convenient path is to add each domain's needs directly into the core — a new column on a core table here, a domain branch in a feature function there — which is exactly the erosion ADR-0001 decision 7 warns against. The first real consumer, IndustryGrow, contributes a domain entity (`production_unit`), cultivation-domain types, and plugin interfaces; if those land *in* the platform, IndustryFlow becomes IndustryGrow's backend in practice no matter what the framing says, and a second domain cannot reuse the core without inheriting cultivation's specifics.

This ADR decides how a domain extends IndustryFlow: what the extension surfaces are, how extensions depend on the platform, and what keeps domain code out of the core. It is deliberately a direction, not a full interface specification — the concrete mechanics are large enough to deserve their own follow-up work and are deferred.

## Decision drivers

- **The core must stay reusable across domains.** Cultivation is the first domain, not the only one; the core earns its "general-purpose" claim only if a second domain can build on it without forking.
- **Domains evolve at their own pace.** A domain's entities, feature definitions, and rules change independently of the platform and should not require core releases.
- **The dependency must point one way.** Extensions depend on the platform; the platform must never depend on, or import, a domain extension — otherwise the boundary is already broken.
- **Most domain knowledge is data, not code.** Feature definitions, sensor mappings, and rule thresholds are configuration; the platform should consume them declaratively rather than encoding them.
- **Some domain logic genuinely needs code.** Custom transforms and detectors cannot always be expressed as config; there must be a code-extension path that does not mean editing core modules.
- **The boundary needs enforcement, not just intent.** ADR-0001 decision 7 is a rule; this ADR has to give it a shape that review can check.

## Decision

1. **The platform defines stable, versioned extension contracts; extensions implement against them and never patch the core.** A domain need is met by an extension point the core exposes, or by adding a new *generic* extension point to the core — never by inserting domain-specific code into a core path. The core ships the contracts; domains ship implementations and configuration.

2. **Configuration is the primary extension surface.** Domain knowledge that is data — feature definitions (generalizing today's `feature_configs`), sensor/measurement mappings, alert-rule definitions, cultivation/operating profiles — is supplied as declarative configuration that the platform loads and applies. This is the low-friction path and covers most domain extension.

3. **Code plugins extend behaviour where configuration cannot.** Custom logic the platform cannot express declaratively — bespoke feature transforms, custom anomaly detectors, model adapters — is provided as plugins registered through a declared platform interface and loaded by the relevant service, not by modifying core modules. The plugin interface is the contract; the plugin is the extension.

4. **Domain data lives in the tenant schema as extension-owned tables, not as columns on core tables.** Domain entities and attributes (e.g. IndustryGrow's `production_unit`) are added within the tenant schema (which ADR-0003 already isolates per tenant) as tables the extension owns and registers, leaving the core's generic entities (equipment, sensor, measurement) unchanged. The core schema does not accrete per-domain columns.

5. **Extensions are versioned against a declared extension-API version.** The platform publishes its extension contracts with a version and a compatibility guarantee; an extension declares the platform version it targets. A breaking change to a contract is a versioned change, not a silent one (consistent with ADR-0000's supersession discipline).

6. **Extensions are packaged and released separately, depending on the platform.** A domain extension is its own package/repository that depends on IndustryFlow; the platform neither vendors nor imports it. IndustryGrow is the reference extension and the proof that the boundary holds. Per ADR-0000, each owns its decisions in its own repository.

7. **The boundary is enforced in review.** A change that puts domain-specific logic, entities, or vocabulary into a core path is rejected in favour of an extension point. "Does this belong in the core or in an extension?" is a standing review question, and the default answer for anything domain-named is "extension."

## Alternatives considered

**A. Fork the platform per domain.** *Rejected:* every domain maintaining its own fork means no shared upstream, divergent cores, and the platform-level fixes (the very cross-cutting decisions these ADRs make) have to be re-applied N times. It is the copy-paste-the-platform version of the failure ADR-0000 forbids.

**B. Bake domain features into the core behind feature flags.** *Rejected:* the core accretes every domain's specifics, the "generic platform" claim becomes fiction, and the single codebase carries a growing pile of domain branches. This is precisely the erosion ADR-0001 decision 7 exists to prevent.

**C. Configuration-only extension, with no code-plugin path.** *Rejected as sole mechanism:* declarative config cannot express arbitrary transforms or detectors, so domains needing custom logic would have nowhere to go but the core. Config is the primary surface (decision 2) but not the only one; decision 3 provides the code path.

**D. All plugins as separate microservices (no in-process extension).** *Considered, not adopted as default:* a network hop per feature computation or detection is heavy for hot-path feature engineering, and forces operational complexity onto every extension. In-process plugins are the default; an out-of-process (service) plugin is permitted where language independence or fault/security isolation justifies the cost. The in-process-vs-service policy is a fork left for the deferred interface work.

**E. Put domain entities in the core schema as nullable per-domain columns.** *Rejected:* it pollutes core tables with every domain's attributes and couples the core schema to domains. Extension-owned tables in the per-tenant schema (decision 4) keep the core generic while still isolating domain data per tenant.

## Consequences

### Positive

- The core stays generic and reusable, so a second and third domain can build on it without forking — the platform thesis becomes real rather than asserted.
- Domains evolve independently of the platform's release cycle, and IndustryGrow's contributions (production_unit, domain types, plugins) land in IndustryGrow, not in the core.
- The one-way dependency and separate packaging make the boundary explicit and checkable, and align with ADR-0000's per-repository ownership.
- The existing config-driven feature engineering is recognized and generalized into a real extension surface rather than an ad-hoc file.

### Negative

- Defining and versioning stable extension contracts is upfront design work and an ongoing compatibility commitment; a contract is a promise the platform must keep.
- A code-plugin interface is a security and stability surface: in-process plugin code runs with the service's privileges, which is a real concern for the commercial-managed deployment (ADR-0001) where plugins may not be fully trusted — a trust model is needed (deferred).
- Keeping domain needs out of the core requires continuous review discipline; the convenient "just add it here" is always available and always wrong under decision 7.
- Multi-package development (platform + extensions) adds release-coordination overhead that a single repository would not have.

## Deferred decisions

- **Concrete plugin interface.** The mechanism (e.g. Python entry points vs an explicit registry), the lifecycle (discovery, loading, configuration), and the per-service interfaces are unspecified here.
- **Extension-API versioning scheme.** How contract versions are expressed and what compatibility guarantee they carry.
- **In-process vs out-of-process policy.** When a plugin must be a service rather than in-process (alternative D), especially for untrusted plugins in managed deployments.
- **Plugin trust model.** How plugin code is vetted/sandboxed/signed for the commercial-managed offering.
- **Extension-owned schema management.** How extension tables register and migrate within per-tenant schemas alongside the platform's own migrations.
- **Dashboards and profiles packaging.** How domain dashboards (Grafana) and operating profiles ship as part of an extension.

## References

- ADR-0001 — IndustryFlow framing; decision 7 (domain specifics live in extensions) that this ADR gives a shape.
- ADR-0003 — tenant→schema resolution; the per-tenant schema where extension-owned domain tables live (decision 4).
- ADR-0000 — decision records & single-source-of-truth; the per-repository ownership the separate-packaging decision relies on.
- IndustryGrow — the reference extension, contributing the production_unit entity, cultivation-domain types, and plugin interfaces on top of the platform.
