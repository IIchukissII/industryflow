<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0030 — Externally-authored models are admitted, but what provenance used to vouch for must be declared and proven

- **ID:** ADR-0030
- **Status:** Proposed
- **Date:** 2026-07-16
- **Project:** IndustryFlow
- **Parent:** [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (the artifact declares, the serving environment satisfies or refuses — the contract this record extends to artifacts the platform never observed being made)
- **Companions:** [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (the one door into tracking, and the single point where the tenant namespace is enforced), [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (a model declares what its output means; the platform never guesses)
- **Related:** [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (the durable history that motivates training off-platform), [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (notebooks as the analytics surface, and the sandbox that bounds the kernel), [ADR-0021](ADR-0021-model-drift-monitoring.md) (the drift lane, which needs a training baseline an uploaded model has no run to carry), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (audience-bound capabilities)

## Context and problem

ADR-0025 made the platform's history durable and readable: raw measurements land in Parquet in the
object store with unbounded retention, and a broker vends prefix-scoped read access. ADR-0011 framed
embedded notebooks as the analytics and experimentation surface — and they are good at that: an
experiment, a chart, a quick look into the data.

They are not where a serious model over years of that history gets trained. That work wants compute
the platform does not offer, GPUs it does not have, and a team's own tooling. So the honest shape of
the requirement is: the history goes out, the finished model comes back. Today only the first half
exists. Every model in the registry was authored in the kernel and logged through the tracking
gateway, because ADR-0019 dec 1 decided that tracking is reached **only** through that gateway. The
word "only" is load-bearing, and it currently has no exception.

The tempting reading is that no new decision is needed. ADR-0027 already says the artifact declares
its requirements and the serving environment satisfies them or refuses — a rule it deliberately made
generic, needing "no amendment when torch arrives". If the artifact is the authority, why should the
platform care where it came from?

Because that contract was written for artifacts whose authoring environment the platform controls,
and three of its properties were never decided by the contract itself. They were **inherited from
provenance**:

- **Requirements were recorded, not asserted.** MLflow observed a real training environment and wrote
  down what was in it. ADR-0027 dec 1 calls the artifact "the authority" — but the artifact earned
  that standing by being the output of an environment the platform built.
- **The empirical check had two real endpoints.** ADR-0027 dec 5 makes it the *authority* over the
  declarative check: a model trained by the real authoring image must load and score in the real
  serving image. That round-trip presupposes an authoring image the platform can run.
- **The bytes came from a sandbox.** ADR-0011 dec 2 makes the kernel untrusted and un-credentialed;
  ADR-0027 deferred "refusing pickle artifacts outright" as the *trust half* of its boundary —
  hardening worth doing, not a breach worth stopping the line for.

Sever provenance and all three quietly change meaning while every line of code still looks correct.
That is the expensive failure: not a crash, but a coherent change that contradicts a decision already
made.

So the question this record answers is not "may users upload models" — the requirement is real and
the platform has already paid for the history that motivates it. It is: **what did the kernel used to
vouch for, and what must an uploaded artifact do instead?**

## Decision drivers

- **Declared and observed are not the same word.** ADR-0027 could treat them as interchangeable
  because provenance collapsed them. This path pulls them apart, and every place the old contract
  said "the artifact says so" must be re-read as "someone typed this".
- **A pickle's blast radius is a function of provenance, not of the file.** This is the crux, and it
  is developed in decision 4.
- **Which singularity is actually still intact.** ADR-0019 dec 5 concentrated the tenant namespace,
  but the tenant *name* rule already has two implementations — the gateway's, and the one the
  browser read-path carries in `ml_service`. What has never been duplicated is the privileged
  principal that may **write** the artifact store (dec 6). A decision that trades on "one enforcement
  point" must say which point it means, or it borrows credit the architecture no longer has.
- **A refusal with a reason is an honest answer** (ADR-0027 dec 2). An open-core platform owes an
  operator a clean "no", not a load-and-hope.
- **The platform never guesses** (ADR-0028 dec 2). An absent declaration is a refusal, never a
  default.
- **A silent wrong answer is worse than a crash** (ADR-0027). The drift lane alerts on these outputs.

## Decision

1. **The platform admits externally-authored model artifacts.** Training off-platform is a supported
   path, not a workaround. ADR-0011 keeps notebooks as the experimentation surface; this record adds
   a way back in for work that outgrew them. ADR-0019 dec 1's "only" gains exactly one exception, and
   it is written here rather than assumed by an implementation.

2. **The exception is one door, and it is the tracking gateway** — because of who may write the
   artifact store, not because of who knows the tenant rule.

   The tempting argument is that the gateway is the single place the tenant namespace is enforced
   (ADR-0019 dec 5). That argument is half spent and this record will not lean on it: the tenant
   *name* rule already lives in two places, since the browser read-path re-derives it inside
   `ml_service` rather than calling the gateway. That duplication was accepted for reads, where the
   cost of getting it wrong is a leak that a prefix re-check catches.

   What is genuinely singular is **write authority over the artifact store** (ADR-0019 dec 6). The
   gateway holds the only privileged object-store principal; `ml_service` deliberately holds no
   object-store credential at all — it reaches artifacts through the tracking server, never the
   bucket. Putting ingestion anywhere else therefore does not *reuse* an existing authority, it
   **mints a second one**: a new privileged writer to the store where every tenant's models live.
   That is the cost this decision refuses to pay, and it is a different and better reason than the
   one about namespaces.

   The gateway was built for interactive tracking, not artifact ingestion, so this is a real cost
   paid deliberately — a component takes on a shape it was not designed for, in exchange for the
   number of privileged writers staying at one.

3. **An upload is authorised by the platform session, under its own audience — never by a tracking
   capability.** ADR-0019 dec 3 mints tracking capabilities for the authoring kernel; they are
   per-session, un-human, and bound to a spawn. An upload is a person, at a browser or a CLI, holding
   a platform session (ADR-0004). Reusing the kernel's audience would hand the upload surface to every
   running kernel — precisely the conflation ADR-0015 dec 3's audience-binding exists to prevent.

4. **Only formats that can be loaded without executing author-supplied code are accepted; a pickle is
   refused at the gate.** The supported serialisations are those ADR-0027 dec 3 already governs —
   `skops` for scikit-learn, the native booster for xgboost — and this path additionally requires that
   the loader never fall back to pickle, which it does by default
   (`MLFLOW_ALLOW_PICKLE_DESERIALIZATION` defaults true).

   This resolves, for this path only, what ADR-0027 deferred as "the trust half of this boundary".
   It is deferred there and urgent here, and the difference is entirely provenance:

   > For a kernel-authored artifact, a pickle grants its author code execution in an environment
   > where they already had code execution — their own sandboxed kernel (ADR-0011 dec 2/7). It buys
   > an attacker nothing they did not already hold, which is why refusing it was hardening. For an
   > uploaded artifact, the author has no foothold at all. A pickle would be the platform *granting*
   > arbitrary code execution, inside `ml_service`, on the trusted side of the boundary, with
   > `ml_service`'s credentials, to anyone who can authenticate a session.

   Same format, same loader, same line of code — different blast radius, because provenance changed.
   Refusing pickle is therefore not hardening on this path. It is the **precondition for admitting
   uploads at all**, and if it cannot be enforced, the rest of this record does not apply.

5. **The artifact's output semantics are declared explicitly at upload, or the upload is refused.**
   ADR-0028 dec 1 made declared semantics a first-class fact and dec 2 forbade inferring them from a
   prediction's value or dtype. The kernel emits that declaration today; an external author has no
   kernel to emit it from, so the declaration becomes a required input. There is no default and no
   fallback: an upload without a semantics from the vocabulary ADR-0028 governs is refused, exactly as
   a guess would be.

6. **Requirements are declared explicitly and completely by the uploader — and are treated as an
   assertion, not a record.** ADR-0027 relies on MLflow's *inference* of a training environment, and
   already knows that inference is incomplete (it misses numpy). For an uploaded artifact there is no
   training environment to infer from at all; whatever the artifact carries is what someone wrote.
   The declarative check still runs — it reads what the serving image actually installs, which is the
   half ADR-0027 made the artifact unable to lie about — but its input is now an assertion, and this
   record names it as one rather than letting "the artifact is the authority" carry a weight it no
   longer bears.

7. **The empirical check is replaced, not dropped.** ADR-0027 dec 5 requires both checks and makes the
   empirical one the authority; an uploaded artifact has no authoring image, so the round-trip cannot
   run. The substitute is that the artifact must **load and score in the real serving image**, in an
   isolated process, against its declared signature, before it may be deployed. This is weaker and the
   weakness must be stated plainly: it proves the artifact works *here*, not that *here* matches where
   it was made. Its value is that it converts the one thing the platform can still observe — whether
   this environment can honour this file — into evidence, instead of accepting the uploader's word for
   both halves. Dropping the check to a version comparison would leave only assertions checked against
   assertions, which ADR-0027 dec 5 rejects.

8. **Provenance is recorded as a first-class fact, and never synthesized to look internal.** A model
   version carries whether it was kernel-authored or uploaded. The platform does not fabricate a
   tracking run to make an uploaded artifact fit the shape the registry already knows, however
   convenient that would be: a synthetic run makes an environment the platform never observed look
   observed, which is the same category of lie ADR-0028 was written to stop. It also has a concrete
   consequence rather than only an aesthetic one — the drift lane (ADR-0021) scores against a training
   baseline that an uploaded model has no run to carry, so an uploaded model is either accompanied by
   a declared baseline or is **visibly** un-monitorable. Visibly is the decision; silently is the
   failure.

9. **This record does not admit torch, or any flavor outside ADR-0027's supported set.** An upload
   path makes an unsupported flavor *more likely to arrive*, not more servable. Such an artifact is
   refused at the gate with a reason, exactly as ADR-0027 dec 2 already provides. The out-of-process
   serving boundary remains ADR-0028 dec 7's record to write, and this one deliberately does not
   pre-empt it.

## Alternatives considered

- **A. Point the existing registration gate at an uploaded file; change nothing else.** The reading
  that provenance is irrelevant because the artifact is the authority. Rejected: it silently converts
  three inherited guarantees into assertions (Context), and it inherits a pickle loader whose blast
  radius has changed underneath it. It is the cheapest option and the one that looks correct.
- **B. A dedicated upload service, or `ml_service`, with its own object-store principal.** Cleaner to
  build — the gateway is not shaped for bulk ingestion, and `ml_service` already holds the tenant name
  rule and already talks to the tracking server as a trusted component. Rejected on decision 2's
  narrower ground: either would need an object-store write credential it does not have today, making a
  second privileged writer to the bucket holding every tenant's models. The convenience is real; the
  new authority is permanent.
- **C. Refuse external models; require training in the kernel.** Coherent, and what the architecture
  says today. Rejected because it answers a real requirement with "use the tool that cannot do it":
  the cold layer exists precisely so history outlives the hot store, and no notebook is going to train
  over years of it.
- **D. Accept pickle but sandbox the load.** Trades a clear structural refusal for a containment
  problem that must then hold forever. Refusing a format is checkable; sandboxing arbitrary code is a
  standing bet.
- **E. Accept uploads, defer the semantics declaration, infer it for now.** Directly contradicts
  ADR-0028 dec 2, and #236 is what inference costs.

## Consequences

### Positive

- The requirement is met on the platform's own terms: history goes out through ADR-0025, a finished
  model comes back through one mediated door.
- ADR-0027's deferred trust half is resolved where it became load-bearing, with a reason that explains
  why it can stay deferred for kernel-authored artifacts.
- The number of privileged writers to the artifact store stays at one, which is the property that was
  still intact and worth keeping.
- The distinction between *recorded* and *asserted* becomes a fact the platform holds, rather than an
  assumption its checks quietly depend on.

### Negative

- **The gateway takes on a shape it was not designed for.** Interactive tracking and artifact
  ingestion have different sizes, timeouts, and failure modes. This is the price of decision 2.
- **An uploaded model is held to a genuinely weaker gate than a kernel-authored one**, and no amount
  of wording fixes that — the round-trip check has one real endpoint instead of two. The platform can
  prove the artifact works here; it cannot prove here resembles there.
- **Refusing pickle will refuse real models** that external authors produced with default tooling,
  since pickle is what most frameworks reach for. The refusal is correct and it will still be
  experienced as friction.
- **Uploaded models may be un-monitorable by ADR-0021** until a baseline accompanies them, which
  narrows the drift lane's coverage exactly where a model is least understood.
- The supported-flavor refusal (decision 9) will be met most often by uploads, so the pressure for
  ADR-0028 dec 7's record arrives through this door.

## Deferred decisions

- **The training baseline / `reference_profile` for uploaded models.** Decision 8 requires it be
  declared or the model be visibly un-monitorable; *where it lives and what shape it takes* is the
  same gap ADR-0021 already defers, and ADR-0028 approaches from the autoencoder-scale side. All three
  should be closed by one record, not three.
- **Artifact size, transfer, and resumability.** A model trained on years of cold-layer history is not
  a notebook-sized file; whether ingestion is a direct transfer, a gateway-minted scoped upload, or
  something staged is an implementation concern this record does not settle.
- **Attestation and signing of external artifacts.** Decision 6 accepts an assertion. Whether an
  uploaded artifact must eventually carry a verifiable claim about who built it and from what, is the
  supply-chain question proper — and is genuinely separate from admission.
- **Whether an uploaded model may occupy the same stages/promotion path** as a kernel-authored one, or
  whether provenance (decision 8) constrains promotion.
- **Turning pickle off for the kernel-authored path too.** Decision 4 resolves ADR-0027's deferral only
  for uploads. The notebook path keeps the deferral, with its original reasoning intact.
- **Whether the tenant name rule should have two implementations at all.** Decision 2 records that it
  already does — the gateway's and `ml_service`'s — and declines to treat that as an argument. Whether
  the two should converge, and which owns the rule, is a live question this record surfaces without
  settling. ADR-0019 dec 5 reads as though the answer is one; the code has said two since the browser
  read-path existed.

## References

- ADR-0027 — the declare/satisfy-or-refuse contract this record extends; dec 3 (supported flavor set,
  serialisation), dec 5 (both checks required, empirical is the authority), and the deferred "trust
  half" that decision 4 resolves for this path.
- ADR-0019 — dec 1 (tracking reached only through the gateway — the "only" this record excepts), dec 3
  (tracking-capability audience), dec 5 (the single tenant-namespace enforcement point), dec 6 (the
  privileged artifact-store principal).
- ADR-0028 — dec 1/2 (declared semantics; never guess), dec 7 (the out-of-process record decision 9
  refuses to pre-empt).
- ADR-0025 — the cold layer whose durable history motivates off-platform training.
- ADR-0011 — dec 2/7 (the untrusted, sandboxed kernel whose bound decision 4 turns on).
- ADR-0021 — the drift lane and the training baseline an uploaded model has no run to carry.
- ADR-0015 — dec 3, audience-bound capabilities.
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
