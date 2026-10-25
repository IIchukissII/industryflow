<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0036 — Out-of-process model runtime for frameworks the serving image does not carry

- **Status:** Accepted
- **Date:** 2026-10-25
- **Parent:** [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md)
- **Related:** [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (dec 7: the boundary
  this record settles), [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (declare, satisfy or
  refuse; the gates), [ADR-0030](ADR-0030-externally-authored-model-admission.md) (dec 4: no author code
  on load; the upload path), [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (dec 3: outages are
  retried), [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (toggles, network policy),
  [ADR-0035](ADR-0035-drift-baseline-derived-scores.md) (baseline scores derived at the gate)

## Context

The serving image is one pinned stack, and a framework it does not carry is refused at the gate.
Neural-network frameworks and accelerators are the common case of that refusal; carrying one in the
serving image puts a multi-gigabyte layer and a second lockstep parity burden on every deployment,
including self-hosters who never use it. Their native artifact formats deserialise executable objects
or need author code to rebuild the model, which the serving side does not run. Accelerators are a
property of where a process is placed. The serving service is the only reader of the artifact store and
owns every refusal and every score's meaning.

## Decision

1. **A framework the serving image does not carry is served by a separate model runtime, one per
   framework stack, deployed only where it is wanted.** A deployment without it refuses those models at
   the gate with a reason; that is the same platform with a smaller supported set, and nothing falls back.
2. **The runtime computes the model's raw output only; detectors, semantics and every refusal stay in
   the serving service.** The meaning of a score keeps one home (ADR-0028 dec 1-3).
3. **The runtime is reachable only from the serving service, holds no credential and has no egress.**
   The serving service hands it the artifact's bytes, which the runtime keys by content digest.
4. **The runtime keeps nothing of a request once it has answered and logs no input.** It is shared by
   every tenant, so it holds no tenant's data between calls.
5. **Only an open exported-graph format, loadable without author code, is admitted.** For the torch
   family that is ONNX; the framework's native and scripted formats and weights that need author
   architecture code are refused, so the framework itself never runs on the serving side.
6. **The artifact declares the graph format version it was exported at; a runtime declares the range it
   satisfies and joins the served supported set only while deployed.** A version outside that range is
   refused (ADR-0027 dec 1-2, ADR-0028 dec 5).
7. **The deployment gate loads and scores the artifact in the live runtime.** With no runtime deployed
   nothing passes it; the gate is never simulated or replaced by a version comparison.
8. **Models for a runtime are authored off the platform and arrive by the upload path.** The authoring
   kernel gains no framework, and every such model meets ADR-0030 in full.
9. **An unreachable runtime fails its models' inference as an infrastructure failure, retried like any
   outage and never routed to another runtime.** An accelerator is where a runtime is placed, never a
   property of a model.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The framework in the serving image | A multi-gigabyte layer and a lockstep parity burden for every deployment (ADR-0028) |
| 1 | A runtime per model or per tenant | Multiplies processes and accelerators with nothing tenant-specific to isolate |
| 1 | A general-purpose model server product | Brings its own model store, loaders for executable formats and a registry beside the platform's |
| 1 | Falling back to another runtime when one is missing | Serves a model under a stack it was never admitted to |
| 2 | Detectors inside the runtime | A second home for score semantics, drifting from the first |
| 3 | The runtime reading the artifact store itself | A second reader and a credential on a process that runs every tenant's models |
| 3 | Inference callers reaching the runtime directly | Bypasses the serving service's refusals and semantics |
| 5 | The framework's native serialised format | Deserialises executable objects on load (ADR-0030 dec 4) |
| 5 | The framework's scripted-module format | Runs author code, interpreted instead of imported |
| 5 | Weights plus author-supplied architecture code | Author code on the trusted side |
| 6 | Admitting any graph and relying on the gate alone | The refusal would come at deployment for what registration could already say |
| 7 | A build-time round trip in place of the live gate | No authoring image exists for these models; only the live runtime is where they serve |
| 8 | An authoring kernel image carrying the framework | A multi-gigabyte per-user image and a second parity boundary on the authoring side |
| 9 | Dead-lettering readings while the runtime is down | An outage would drain valid readings instead of delaying them (ADR-0005 dec 3) |
| 9 | Accelerator need declared by the model | Couples an artifact to a deployment's hardware |

## Consequences

**Positive**
- Neural-network models can be served without growing the serving image or the self-hosted footprint.
- No new code-execution path: the graph is interpreted by the runtime, never author code.
- Such models get a drift baseline like any other, scored through the runtime at the gate (ADR-0035).

**Negative**
- A second serving process to deploy, size and watch, and an internal hop on every inference it serves.
- Models whose operations do not export to the graph format cannot be served.
- A runtime outage stops scoring for every model it serves.

## Deferred

| Open question | Settled by |
|---|---|
| Building the runtime | The first model a tenant needs that the serving image cannot carry |
| Batching and throughput limits across tenants | First runtime under real load |
| Runtimes for framework families other than torch | The first such family requested |

## References

- [docs/architecture/ml-and-features.md](../docs/architecture/ml-and-features.md): detectors, the served supported set.
- `services/ml_service/api/load_probe.py`: the deployment gate's isolated load-and-score for uploads.
