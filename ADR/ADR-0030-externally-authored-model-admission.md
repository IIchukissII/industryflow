<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0030 — Admission of externally-authored models

- **Status:** Accepted
- **Date:** 2026-07-16; changed 2026-10-29
- **Parent:** [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (the artifact declares; the serving environment satisfies or refuses)
- **Related:** [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (sole writer of the artifact store), [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (declared semantics), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (capability audiences), [ADR-0035](ADR-0035-drift-baseline-derived-scores.md) (the declared baseline, dec 8), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (the public edge), [ADR-0037](ADR-0037-no-author-code-on-the-serving-side.md) (dec 4's rule for every provenance), [ADR-0039](ADR-0039-one-tenant-ownership-test.md) (the one test of a tenant's upload area)

## Context

Serious models over the cold layer's history are trained off-platform, and no decision covered an
artifact the platform never observed being made. ADR-0027 inherited three properties from kernel
provenance: requirements recorded by an observed environment, an empirical check with two real ends,
and bytes from a sandbox whose author already had code execution. An upload has none of these, so an
artifact that runs author code on load would grant code execution on the trusted side.

## Decision

1. **The platform admits externally-authored model artifacts.** Off-platform training is a supported
   path; notebooks stay the experimentation surface.
2. **The tracking gateway is the one door for the bytes, because it is the store's only writer.** It
   refuses on structure; admission policy is the serving side's, which holds only a read principal scoped
   to the tenants' upload areas, and only the store's bootstrap holds its root credential.
3. **An upload is authorised by an upload capability audience of its own, demand-minted by the serving
   side from a verified session, never by a tracking capability.** The mediator resolves that opaque
   handle and never verifies a session, since a verifier of the symmetric signature can forge one.
4. **Only artifacts loadable without executing author-supplied code are admitted.** Executable
   serialisation, an author-chosen loader or imported module, shipped code and author-defined classes are
   refused at registration, deployment and every load, which uses only platform loaders for admitted
   flavors, with deserialisation off and no typed-format grant (ADR-0027 dec 13).
5. **The uploader declares the output semantics, judged at the registration gate.** An absent, unknown or
   unimplemented declaration for the artifact's flavor is refused; there is no default.
6. **The uploader declares requirements completely, treated as an assertion, not a record.** The
   declarative check still reads what the serving image installs.
7. **The empirical check is replaced, not dropped.** At the deployment gate the artifact must load and
   score in the real serving image, in an isolated process, which proves it works here only.
8. **Provenance is a first-class fact, never synthesised to look internal.** An upload is admitted only
   at one upload under the caller's tenant's uploads area, checked at registration, deployment and every
   load, and carries a declared baseline or is visibly un-monitorable.
9. **This record admits no flavor outside ADR-0027's supported set.** A runtime that serves a framework
   out of process widens that set by ADR-0036, not here.
10. **The upload plane is reachable through the one public edge, and bytes go client to store under a
    single-object presigned write scoped to a staging area outside every tenant namespace.** The edge
    serves plane and staging by sub-path on the app's own origin, so the store has no cross-origin policy.
11. **Provenance adds no step to deploying an upload beyond the gates already decided.** A user with a
    writing role deploys an upload that passes the deployment gate as they deploy any model (ADR-0004
    dec 5); the gate's empirical check (dec 7), the no-author-code rule (ADR-0037 dec 1) and the
    provenance checked at every load (dec 8) are where an upload's weaker origin is answered.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| — | Point the existing registration gate at an upload, change nothing else | Turns three inherited guarantees into assertions and keeps a loader whose blast radius changed |
| 1 | Refuse external models; train only in the kernel | Answers a real requirement with an environment that cannot meet it |
| 2 | An upload service, or the serving side, with a write principal | A second privileged writer to every tenant's models |
| 2 | Admission policy judged at the gateway | Puts a second, hand-kept copy of the semantics vocabulary and detector registry outside the serving side |
| 2 | A serving side with no store credential, reaching uploads through the tracking server | Needs the fabricated run decision 8 refuses and a broader reader of every tenant's uploads |
| 3 | Reuse the kernel's tracking capability audience | Hands the upload surface to every running kernel and erases the one fact that separates the two populations' rules |
| 3 | The mediator verifying the platform session itself | A verifier of the symmetric signature can forge a session for any user in any tenant |
| 4 | Sandbox an executable load | A containment bet that must hold forever |
| 4 | A closed list of frameworks whose formats are safe | Needs amending for every framework and refuses a safe format nobody listed |
| 4 | The framework's deserialisation switch as the control for typed formats | That switch governs executable formats only; typed loads are controlled by the serving side's provenance grant |
| 5 | Infer the semantics until declarations exist | Contradicts ADR-0028 dec 2 |
| 5 | Judge the declaration at the deployment gate | A model whose output means nothing here would sit in the registry in a state it cannot describe |
| 7 | A version comparison in place of the empirical check | Checks assertions only against assertions |
| 7 | A load-and-score path beside the deployment gate | A second answer to a question the deployment gate already answers |
| 8 | A synthesised tracking run for an upload | Makes an environment the platform never observed look observed |
| 10 | Relay bytes through the mediator | Couples it to artifact size (ADR-0019 dec 6) |
| 10 | Only an in-network client, the browser deferred | Re-opens decision 3, which already made the browser a client |
| 10 | A cross-origin allowlist on the store for the app's origin | One origin through the edge needs no cross-origin exposure at all |
| 11 | An administrator role or an explicit acknowledgement to deploy an upload | A second answer, by role, to a risk the gate and every load already answer by checking the artifact |
| 11 | Uploads serving only in a restricted status until a further condition holds | A serving state with no defined exit, and a model that passed every gate kept from the use it was admitted for |

## Consequences

**Positive**
- A model trained off-platform comes back through one mediated door, and the store keeps one writer.

**Negative**
- The gateway takes on ingestion, with different sizes, timeouts and failure modes.
- Unadmitted bytes rest in a staging area no tenant can address and nothing loads, with its own expiry.
- An upload passes a weaker gate, and models produced with default tooling are refused.
- The object store gains an external route that is safe only while presigned-only and staging-scoped.

## Deferred

| Open question | Settled by |
|---|---|
| Resumable or multipart transfer of a very large artifact | First upload one request cannot carry |
| Attestation and signing of external artifacts | The supply-chain record |

## References

- `services/notebook_tracking_gateway/admission.py`, `staging.py`: structural refusal, staging.
- `services/ml_service/api/uploaded_model.py`, `skops_trust.py`, `load_probe.py`: decisions 4-8.
- `services/frontend/nginx.conf.template`, `infrastructure/minio/minio-init.sh`: decisions 10 and 2.
