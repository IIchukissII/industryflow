<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0010: Extension plugin mechanism — in-process registry and the feature-transform contract (rev 2)

- **ID:** ADR-0010
- **Status:** Accepted
- **Date:** 2026-06-27 (rev 1: 2026-06-29; rev 2: 2026-10-13)
- **Project:** IndustryFlow
- **Parent:** ADR-0008 (extension and plugin interface)
- **Companions:** ADR-0003 (tenant→schema resolution), ADR-0001 (framing)
- **Refined by:** ADR-0024 (the transform contract's stateful-class tag), ADR-0028 (the model-adapter contract this record deferred)
- **Supersedes:** ADR-0010 rev 0 (named the anomaly detector only as future work; rev 1 adds it as decision 6); rev 1 (decision 3 stated one compatibility rule for every major, which the pre-stable contract did not keep; rev 2 states the 0.x rule explicitly)

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

**Revision 1.** Decision 2 (rev 0) named the anomaly detector as one of the *later contracts*
the registry pattern would template, "declared as future work, not built here." That contract
has since been built on exactly the registry mechanism this ADR fixes. Recording it as a new
decision (decision 6) rather than as an after-the-fact addendum keeps it inside the supersession
discipline (ADR-0000 dec 5): a new contract is a decision, so it earns a revision, not an
in-place note. The mechanism is unchanged from rev 0; this revision only fixes a second contract
on it.

**Revision 2 (2026-10-13).** Decision 3 said a breaking contract change is a major bump, never silent. The contract has been at major 0 since it was published, and has broken without one: ADR-0023 rev 1 removed an element of the transform context as "a minor at extension-API 0.x", and ADR-0024 then bumped the minor for an additive change, recording in passing that the earlier break had not been versioned at all. Read literally, decision 3 forbade the first; in practice the project was applying semantic versioning's rule for major 0 — anything may change — without having decided it. A rule followed only in a form nobody wrote down is the drift ADR-0000 dec 4 forbids. Rev 2 decides the 0.x rule explicitly, keeps rev 0's rule for 1.0 onward, and names what "never silent" requires at each.

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
   module import, not by editing core.** A plugin is a function (or class) passed to a
   platform-provided registration decorator that adds it to a process-global registry keyed by
   name. The relevant service imports the platform's built-in plugin module (always) plus any
   modules an operator names in its configuration; importing a module runs its registrations.
   The core never imports a named extension. (The configuration name, decorators and signatures
   are in `docs/operations/extensions.md`.)

2. **The first contract is the feature transform.** A transform is an asynchronous function of
   the transformation's configuration, the current reading, and a platform-supplied runtime
   context, returning one feature value; it is registered under a transform *type* name. The
   engine dispatches a transform type through the registry instead of a hardcoded branch. The platform's own generic transforms (identity,
   polynomial, interaction, deviation, statistical, rolling_stat) are registered built-ins with
   no domain knowledge; a domain registers new transform *types* the same way, in its own module.
   The same registry pattern is the template for later contracts: the anomaly-detector
   contract is now fixed in decision 6 (rev 1), and the model-adapter contract is ADR-0028's.

3. **Contracts carry an extension-API version with a compatibility rule *(rev 2)*.** The platform
   publishes an extension-API version (semver). An extension module may declare the version it
   targets; the loader accepts it when the **major** versions match and the platform minor is at
   least the target's, and refuses otherwise with a clear error. What a bump promises depends on
   the major:

   - **From 1.0, the contract is stable.** A breaking change is a major bump, and the loader
     refuses an extension built for another major. This is rev 0's rule, unchanged.
   - **At 0.x, the contract is pre-stable, and a minor bump may break it.** An addition is a minor
     bump; so is a removal or change of an existing element, because at major 0 the minor is the
     only axis there is. The loader's arithmetic is the same, so an extension written against an
     earlier 0.x minor still loads, and fails when it reaches for an element that was removed. That
     is the accepted price of a contract still being shaped by its first users.

   **Never silent, at either major, means recorded, and versioned.** A change that breaks the
   contract bumps the version and is made by an ADR or a revision that says, in its own text,
   which element broke and what replaces it — the number alone is not the announcement (ADR-0000
   supersession discipline; ADR-0008 decision 5). Leaving 0.x is itself a decision, taken by a
   revision of this record.

   > *Rev 0 read:* "A breaking contract change is a major bump, never silent." True from 1.0; at 0.x
   > it was never followed, and is replaced by the rule above.

4. **Plugins are in-process by default.** In-process execution is the default for hot-path work
   (ADR-0008 alternative D). An out-of-process/service plugin is permitted where isolation or
   language independence justifies the cost, but no out-of-process transport is specified here.

5. **A reference extension demonstrates the boundary; the platform ships no domain config.** The
   Tennessee-Eastman artifacts move out of the core into an in-repo reference extension — its
   feature configuration and a sample domain transform that registers through the contract. The
   core loads it only when configuration names it, so the platform does not import it by default
   (ADR-0008 decisions 6, 7). The sibling project remains
   the real reference extension in its own repository.

6. **The second contract is the anomaly detector *(rev 1)*.** A detector is an asynchronous
   function of the feature vector, the loaded model, the rule's threshold and the runtime context,
   returning a detection result; it is registered under a detector name. The ML inference path
   dispatches the detector a model record names (defaulting to the generic built-in that
   reproduces the previous scikit-learn scoring) through the registry instead of a hardcoded
   scorer, so a domain can register a custom detector — including a model-free one — without
   editing the inference path. The reference extension adds an illustrative rule-based detector.
   This is the same registry mechanism as decision 2, applied to the second of the contracts
   decision 2 named; the model-adapter contract is ADR-0028's.

## Alternatives considered

**A. setuptools entry points for discovery.** *Deferred, not rejected:* entry points are the
right answer for separately-installed extension packages and compose cleanly with this registry
(an entry point simply imports a module that registers). They add packaging ceremony that the
in-repo reference and early extensions do not need yet; configured module import is the
minimal mechanism now and entry points layer on later without changing the contract.

**B. Keep the `if/elif` dispatcher and just add a config flag per domain.** *Rejected:* this is
ADR-0008 alternative B (domain branches behind flags in the core) at the function level.

**C. Make every transform an out-of-process call.** *Rejected as default:* a network hop per
feature per reading is unacceptable on the inference hot path (decision 4 / ADR-0008 alt D).

**D. Keep model scoring hardcoded to scikit-learn in the inference scorer *(rev 1)*.**
*Rejected:* it is the anomaly-detection analogue of the `if/elif` transform dispatcher decision 1
removed — a domain that needs a different (or model-free) detector would have to edit the core
scorer. Decision 6 dispatches the detector through the same registry, with `sklearn` as the
generic built-in, so the default behaviour is unchanged while the core stops being the only place
a detector can live.

**E. Keep decision 3 as written and spend the major on the next break *(rev 2)*.** *Rejected:* it
would declare the contract stable while its first users are still shaping it, and spend 1.0 on
whichever breaking change happens to come next rather than on a decision that the contract is done.
Pre-stability is honest about where the contract is; what was missing was the record of it.

## Consequences

### Positive

- A domain adds a transform by shipping a module and naming it in configuration; the core
  dispatcher is never edited. The boundary ADR-0008 asserts becomes mechanically real.
- The generic transforms are now a declared, versioned contract rather than a private `if/elif`.
- The Tennessee-Eastman artifact leaves the core, and the reference extension proves the path.
- The inference scorer is registry-dispatched too (decision 6): a domain ships a custom or
  model-free detector without editing the core scorer, the same way it ships a transform.

### Negative

- A process-global registry is shared mutable state; double registration or import-order
  surprises are possible and must be guarded (a re-registration is an error, not a silent
  overwrite).
- In-process plugin code runs with the service's privileges — the trust concern ADR-0008 raised
  for managed deployments is unaddressed here (deferred).
- The configured module list is an operational trust boundary: whatever it names is imported and runs.
- **rev 2:** while the contract is at 0.x, an extension author must read the record of every minor
  bump; a loader that accepts an extension is not a promise that every element it uses still
  exists.

## Deferred decisions

- ~~**Model-adapter contract.**~~ *Refined by ADR-0028,* which writes the third contract — a
  detector declaring the score semantics it implements and the artifact flavors it scores — and
  carries its own deferral of the out-of-process serving boundary.
- **Entry-point discovery** for separately-installed extension packages (alternative A).
- **Plugin trust/sandboxing** for untrusted plugins in the commercial-managed offering
  (inherited from ADR-0008).
- **Extension-owned tables and migrations** within per-tenant schemas (ADR-0008 decision 4 /
  its deferred schema-management item).

## References

- ADR-0008 — extension and plugin interface; the direction this ADR makes concrete.
- ADR-0003 — tenant→schema resolution; where extension-owned domain data lives.
- ADR-0000 — decision records & supersession; the versioning discipline for contracts.
