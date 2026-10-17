<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0010 — Extension plugin mechanism: in-process registry and contracts

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-17
- **Parent:** ADR-0008
- **Related:** ADR-0024 (the stateful-class tag), ADR-0028 (the model-adapter contract), ADR-0023
  (the transform context)

## Context

ADR-0008 requires code plugins behind versioned contracts. Feature transforms run per reading on the
inference hot path, so the default plugin adds no network hop, and the platform loads only what
configuration names.

## Decision

1. **Plugins register into an in-process registry; the platform discovers them by configured module
   import.** Registering a name twice is an error, and the core never imports a named extension.
2. **The first contract is the feature transform.** An asynchronous function of its configuration,
   the reading and a runtime context returns one value, dispatched by type through the registry; the
   generic transforms are built-ins.
3. **Contracts carry a semantic extension-API version with a compatibility rule.** The loader accepts
   an extension whose major matches and whose target minor is not above the platform's, and refuses
   it otherwise with a clear error.
   - **From 1.0** a breaking change is a major bump.
   - **At 0.x** a minor bump may add, remove or change an element.

   A breaking change at either major bumps the version and is made by an ADR change naming the broken
   element and its replacement. Leaving 0.x is a change to this record.
4. **Plugins are in-process by default.** An out-of-process plugin is permitted where isolation or
   language independence justifies it; no transport is specified.
5. **A reference extension demonstrates the boundary; the platform ships no domain configuration.**
   It carries a domain configuration, a transform and a detector, loaded only when configured.
6. **The second contract is the anomaly detector.** An asynchronous function of the features, the
   model, the threshold and the context returns a detection result; inference dispatches the detector
   a model record names, defaulting to a generic built-in.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Packaging entry points | Not rejected; unneeded ceremony now, layers on later unchanged |
| Hardcoded dispatcher with per-domain flags | Domain branches in the core (ADR-0008 dec 7) |
| Out-of-process transforms | A network hop per feature per reading |
| Scoring hardcoded to one library | A new or model-free detector would edit the core |
| Treat 0.x as stable | Declares the contract finished while it is still being shaped |

## Consequences

**Positive**
- A domain adds a transform or detector by shipping a module and naming it in configuration.

**Negative**
- The global registry is shared mutable state, and import order matters.
- Plugins run with the service's privileges; the configured module list is a trust boundary.
- At 0.x, loading does not promise every element an extension uses still exists.

## Deferred

| Open question | Settled by |
|---|---|
| Entry-point discovery | First extension released as its own package |

Plugin trust and extension-owned tables are deferred in ADR-0008.

## References

- [docs/operations/extensions.md](../docs/operations/extensions.md); `services/ml_service/api/extensions/` (current version).
