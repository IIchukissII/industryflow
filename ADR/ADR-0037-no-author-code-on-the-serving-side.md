<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0037 — No model load on the serving side runs author code, whatever the provenance

- **Status:** Accepted
- **Date:** 2026-10-26
- **Parent:** [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (its deferred executable
  serialisation for kernel-authored artifacts)
- **Related:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (dec 2: the
  kernel is untrusted), [ADR-0030](ADR-0030-externally-authored-model-admission.md) (dec 4: the data-only
  rule this extends; dec 8: provenance), [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md)
  (flavors a detector claims), [ADR-0036](ADR-0036-out-of-process-model-runtime.md) (dec 5: exported
  graphs only), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (the kernel's path to the
  registry)

## Context

The serving service loads every registered model in a process whose database principal reads every
tenant. A model authored in the kernel is still the work of untrusted code, and an executable format
runs whatever its author put in it at the moment it is loaded. Uploads are already held to a data-only
load; kernel-authored models are not, so a notebook author can turn a contained kernel into code
running with cross-tenant reach. Every model registered so far is in a typed, data-only format.

## Decision

1. **No load on the serving side executes author-supplied code, whatever the model's provenance.** A
   kernel author's code execution is contained in their own environment (ADR-0011 dec 2); a load that
   runs their code moves that foothold into the one process that reaches every tenant.
2. **Kernel-authored models are held to the same data-only rule as uploads, checked at the same three
   points.** Executable serialisation, an author-chosen loader, module or class, and shipped code are
   refused at registration, at deployment and before every load (ADR-0030 dec 4). One rule and one list
   of platform loaders serve both provenances.
3. **The framework's executable-deserialisation switch is off for the whole serving process, set by the
   process itself.** No load needs it on, so it is a fixed property of the process rather than state
   toggled around individual loads.
4. **Provenance decides only the typed-format grant.** Kernel-authored artifacts keep the minimal
   tree-type grant and uploads get none (ADR-0027 dec 13); provenance no longer decides whether a load
   may execute anything.
5. **A detector claims only flavors a data-only platform loader serves.** A claim on an executable
   flavor would register models that every load then refuses.
6. **A registered model that fails the rule is refused at its next load and cannot be deployed.** It is
   not served and marked as ADR-0027 dec 10 allows for version drift, because here the hazard is the load
   itself; the remedy is re-logging it in a data-only format.
7. **Experiment artifacts are not judged; the rule binds what the serving side loads.** A kernel may log
   any artifact to its own runs, which only its own sandbox opens.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Trust executable kernel artifacts because their author already runs code in the kernel | Containment in a one-tenant sandbox is not reach from a process that reads every tenant |
| 1 | Restrict model registration to a privileged role | The kernel is untrusted whoever holds the session (ADR-0011 dec 2); a role is not containment |
| 1 | Sandbox an executable load in an isolated process | A containment bet that must hold forever (ADR-0030 dec 4) |
| 1 | Serve executable kernel artifacts through the out-of-process runtime | That runtime admits exported graphs only (ADR-0036 dec 5) and would still run author code beside every tenant's bytes |
| 2 | A separate, wider loader list for kernel-authored models | Two lists that drift, and the wider one is the hole |
| 3 | Turn the switch off only around uploaded loads, under a lock | Shared mutable state coordinated by hand, guarding a value nothing needs on |
| 3 | Rely on deployment configuration to turn the switch off | The framework's default is on, so an omitted setting fails open |
| 5 | Keep the executable claim and let the load refuse | Admits at registration what deployment and serving then refuse |
| 6 | Serve and mark registered executable models | Serving them is the hazard the record removes |
| 7 | Refuse executable artifacts when the kernel logs them | Refuses an author's experiments in a sandbox where nothing trusted opens them |

## Consequences

**Positive**
- The serving process never runs code a model author chose, so a model cannot widen any author's reach.
- One load rule replaces a provenance branch and the coordination around the switch.

**Negative**
- Custom Python models authored in a notebook cannot be served; an author expresses the model in a
  supported data-only format or does not serve it.
- A registered model in an executable format stops being servable without a migration path other than
  re-logging it.
- Each newly supported framework needs a data-only platform loader before its models serve.

## Deferred

| Open question | Settled by |
|---|---|
| A data-only path for custom model logic (an exported graph authored in the kernel) | First served model the supported formats cannot express |

## References

- [ADR-0030](ADR-0030-externally-authored-model-admission.md) dec 4: the data-only rule.
- `services/ml_service/api/skops_trust.py`, `services/ml_service/api/routers/models.py`: decisions 2-4, 6.
- `services/ml_service/api/extensions/builtins_detectors.py`: decision 5.
