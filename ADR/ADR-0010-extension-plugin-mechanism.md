<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0010: Extension plugin mechanism — in-process registry and the feature-transform contract

- **ID:** ADR-0010
- **Status:** Accepted
- **Date:** 2026-06-27
- **Project:** IndustryFlow
- **Parent:** ADR-0008 (extension and plugin interface)
- **Companions:** ADR-0003 (tenant→schema resolution), ADR-0001 (framing)

## Context and problem

ADR-0008 decided the *direction* for keeping the core domain-generic — versioned contracts,
configuration as the primary surface, code plugins where configuration cannot reach, domain
data in extension-owned tables — and explicitly deferred the concrete mechanism to follow-up
work. This ADR is that follow-up (as ADR-0007 was for ADR-0002's deferred PKI): it fixes the
plugin mechanism so the boundary becomes something code and review can rely on, starting with
the surface where the boundary is most visibly crossed today.

That surface is feature engineering. `ml_service` applies feature transforms by a hardcoded
`if/elif` over a transform `type` string (`feature_engineering/engine.py`), and a
domain-specific Tennessee-Eastman feature definition (`feature_configs/tep_reactor_config.json`)
ships inside the platform. A domain that needs a new transform today has no path but to add a
branch to that core dispatcher — exactly what ADR-0008 decision 7 forbids. The per-tenant
feature **configuration** already lives in the database (the config surface of ADR-0008
decision 2 exists); what is missing is the **code-plugin contract** of decision 3.

## Decision drivers

- ADR-0008's decisions 1, 3, 5: stable versioned contracts, a code path that does not edit
  core modules, and an extension-API version.
- The feature hot path is per-reading; the default plugin must not add a network hop
  (ADR-0008 alternative D — in-process is the default).
- The platform must not import any specific extension; it loads what it is configured to load.
- The mechanism should be small and obvious enough that "add a branch to the core" stops being
  the convenient option.

## Decision

1. **Plugins register into an in-process registry; the platform discovers them by configured
   module import, not by editing core.** A plugin is a function (or class) decorated with a
   platform-provided `register_*` decorator that adds it to a process-global registry keyed by
   name. The relevant service imports the platform's built-in plugin module (always) plus any
   modules named in the `EXTENSION_MODULES` setting (a comma-separated list); importing a module
   runs its registrations. The core never imports a named extension.

2. **The first contract is the feature transform.** A transform implements
   `async (transformation: dict, sensor_data: dict, ctx: TransformContext) -> float`, registered
   with `@register_transform("<type>")`. The engine dispatches a transform `type` through the
   registry instead of a hardcoded branch. The platform's own generic transforms (identity,
   polynomial, interaction, deviation, statistical, rolling_stat) are registered built-ins with
   no domain knowledge; a domain registers new transform *types* the same way, in its own module.
   The same registry pattern is the template for later contracts (anomaly detectors, model
   adapters), which are declared as future work, not built here.

3. **Contracts carry an extension-API version with a compatibility rule.** The platform exposes
   `EXTENSION_API_VERSION` (semver). An extension module may declare the version it targets; the
   loader accepts it when the **major** versions match (and the platform minor is ≥ the target),
   and refuses otherwise with a clear error. A breaking contract change is a major bump, never
   silent (ADR-0000 supersession discipline; ADR-0008 decision 5).

4. **Plugins are in-process by default.** In-process execution is the default for hot-path work
   (ADR-0008 alternative D). An out-of-process/service plugin is permitted where isolation or
   language independence justifies the cost, but no out-of-process transport is specified here.

5. **A reference extension demonstrates the boundary; the platform ships no domain config.** The
   Tennessee-Eastman artifacts move out of the core into an in-repo reference extension
   (`extensions/tep-reference/`) — its feature configuration and a sample domain transform that
   registers through the contract. The core loads it only when `EXTENSION_MODULES` names it, so
   the platform does not import it by default (ADR-0008 decisions 6, 7). IndustryGrow remains the
   real reference extension in its own repository.

## Alternatives considered

**A. setuptools entry points for discovery.** *Deferred, not rejected:* entry points are the
right answer for separately-installed extension packages and compose cleanly with this registry
(an entry point simply imports a module that registers). They add packaging ceremony that the
in-repo reference and early extensions do not need yet; `EXTENSION_MODULES` import is the
minimal mechanism now and entry points layer on later without changing the contract.

**B. Keep the `if/elif` dispatcher and just add a config flag per domain.** *Rejected:* this is
ADR-0008 alternative B (domain branches behind flags in the core) at the function level.

**C. Make every transform an out-of-process call.** *Rejected as default:* a network hop per
feature per reading is unacceptable on the inference hot path (decision 4 / ADR-0008 alt D).

## Consequences

### Positive

- A domain adds a transform by shipping a module and naming it in `EXTENSION_MODULES`; the core
  dispatcher is never edited. The boundary ADR-0008 asserts becomes mechanically real.
- The generic transforms are now a declared, versioned contract rather than a private `if/elif`.
- The Tennessee-Eastman artifact leaves the core, and the reference extension proves the path.

### Negative

- A process-global registry is shared mutable state; double registration or import-order
  surprises are possible and must be guarded (a re-registration is an error, not a silent
  overwrite).
- In-process plugin code runs with the service's privileges — the trust concern ADR-0008 raised
  for managed deployments is unaddressed here (deferred).
- `EXTENSION_MODULES` is an operational trust boundary: whatever it names is imported and runs.

## Deferred decisions

- **Anomaly-detector and model-adapter contracts.** Declared as the next contracts on the same
  registry pattern; their signatures are not fixed here.
- **Entry-point discovery** for separately-installed extension packages (alternative A).
- **Plugin trust/sandboxing** for untrusted plugins in the commercial-managed offering
  (inherited from ADR-0008).
- **Extension-owned tables and migrations** within per-tenant schemas (ADR-0008 decision 4 /
  its deferred schema-management item).

## References

- ADR-0008 — extension and plugin interface; the direction this ADR makes concrete.
- ADR-0003 — tenant→schema resolution; where extension-owned domain data lives.
- ADR-0000 — decision records & supersession; the versioning discipline for contracts.
