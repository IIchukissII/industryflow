<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0030 — Externally-authored models are admitted, but what provenance used to vouch for must be declared and proven

- **ID:** ADR-0030
- **Status:** Accepted (rev 1 — implementation surfaced that rev 0 named *components* and *frameworks* where it should have named a *moment* and a *property*, and each imprecision invites a violation of a decision already on record. Rev 1 fixes what "one door" binds (the write path for bytes, not the enforcement point for admission), names where each check runs, and restores decision 4's refusal to a generic property of the serialisation rather than a list of frameworks. No decision is reversed; each is made precise)
- **Date:** 2026-07-16 (rev 1: 2026-07-16)
- **Project:** IndustryFlow
- **Parent:** [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (the artifact declares, the serving environment satisfies or refuses — the contract this record extends to artifacts the platform never observed being made)
- **Companions:** [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (rev 1 — the tracking gateway, and the only principal permitted to write the artifact store, which is what decision 2 turns on), [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (a model declares what its output means; the platform never guesses)
- **Related:** [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (the durable history that motivates training off-platform), [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (notebooks as the analytics surface, and the sandbox that bounds the kernel), [ADR-0021](ADR-0021-model-drift-monitoring.md) (the drift lane, which needs a training baseline an uploaded model has no run to carry), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (audience-bound capabilities)

## Context and problem

ADR-0025 made the platform's history durable and readable: raw measurements land in Parquet in the
object store with unbounded retention, and a broker vends prefix-scoped read access. ADR-0011 framed
embedded notebooks as the analytics and experimentation surface — and they are good at that: an
experiment, a chart, a quick look into the data.

They are not where a serious model over years of that history gets trained. That work wants compute
the platform does not offer, GPUs it does not have, and a team's own tooling. So the honest shape of
the requirement is: the history goes out, the finished model comes back. Today only the first half
exists: every model in the registry was authored in the kernel and logged through the tracking
gateway, because that is the only way in anyone ever built.

An existing decision appears to forbid the second half: ADR-0019 dec 1 states that tracking is
reached **only** through the gateway. It does not forbid it, and the distinction determines which
record is owed. That "only" governs **the kernel and its credentials** — it is what keeps the
tracking backend and object-store secrets out of an untrusted environment. The same record's dec 7
promises a trusted-side surface, "the platform's own scoped API/UI", and that surface exists,
reaching the tracking server directly as trusted code (ADR-0013 dec 1).

The obstacle is therefore not a decision that refuses. It is that **no decision addresses the
question**: nothing on record contemplates an artifact the platform never observed being made.
Silence is not permission (ADR-0000), and it is a weaker starting point than a prohibition, which
would at least have to be argued against.

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
  browser read-path re-derives on the trusted side. What has never been duplicated is the privileged
  principal that may **write** the artifact store (dec 6). A decision that trades on "one enforcement
  point" must say which point it means, or it claims a property the architecture no longer holds.
- **A refusal with a reason is an honest answer** (ADR-0027 dec 2). An open-core platform owes an
  operator a clean "no", not a load-and-hope.
- **The platform never guesses** (ADR-0028 dec 2). An absent declaration is a refusal, never a
  default.
- **A silent wrong answer is worse than a crash** (ADR-0027). The drift lane alerts on these outputs.

## Decision

1. **The platform admits externally-authored model artifacts.** Training off-platform is a supported
   path, not a workaround: ADR-0025 made the history durable so that it could be used, and training
   over that horizon exceeds what an embedded environment is intended to carry. ADR-0011 keeps
   notebooks as the experimentation surface; this record admits the work that outgrows them.

   This decision excepts nothing. It fills the silence named in the Context — which is why it needs
   to exist at all, and why it could not be settled by pointing an existing gate at a new kind of
   file.

2. **The exception is one door, and it is the tracking gateway** — because of who may write the
   artifact store, not because of who knows the tenant rule.

   > **rev 1 (2026-07-16): what "one door" binds.** It binds **the bytes, not the request**. The
   > gateway is the exclusive *write path to the artifact store*, because it alone holds that
   > credential — it is not thereby the enforcement point for admission policy. Rev 0 named the
   > component without naming the moment, and the available misreading is expensive: it would put
   > ADR-0028's semantics vocabulary and detector registry inside the gateway, which is precisely the
   > hand-maintained second list ADR-0028 forbids, and it would ask a component that is not the
   > serving image to judge what the serving image can honour.
   >
   > So: **the gateway refuses on structure** — what the bytes *are* (decision 4), which it can
   > determine without loading them, without ML libraries, and without a vocabulary. **Admission
   > policy is the serving side's**, at the two gates it already operates (decisions 5, 6, 7). This
   > is not a concession; it is the existing shape. A kernel-authored model already takes exactly
   > this route — bytes to the object store through the gateway, then policy at the serving side's
   > registration gate — and an uploaded artifact earns no different treatment for arriving by a
   > different door.

   The available argument is that the gateway is the single place the tenant namespace is enforced
   (ADR-0019 dec 5). That argument no longer holds in full, and this record does not rest on it: the
   tenant *name* rule already lives in two places, since the trusted read-path dec 7 promised
   re-derives it rather than calling the gateway. That duplication was accepted for reads, where the
   cost of an error is a leak that a second prefix check catches.

   What remains singular is **write authority over the artifact store** (ADR-0019 dec 6). The gateway
   holds the only privileged object-store principal; the serving side holds no object-store
   credential at all, reaching artifacts through the tracking server rather than the store itself.
   Siting ingestion elsewhere therefore does not *reuse* an existing authority; it **establishes a
   second one** — a further privileged writer to the store in which every tenant's models reside.
   That is the cost this decision declines, and it is a narrower and sounder ground than the
   namespace argument.

   The gateway was built for interactive tracking rather than artifact ingestion, so the cost is
   real and is paid deliberately: a component takes on a shape it was not designed for, in exchange
   for the number of privileged writers remaining at one.

3. **An upload is authorised by the platform session, under its own audience — never by a tracking
   capability.** ADR-0019 dec 3 mints tracking capabilities for the authoring kernel; they are
   per-session, un-human, and bound to a spawn. An upload is a person, at a browser or a CLI, holding
   a platform session (ADR-0004). Reusing the kernel's audience would hand the upload surface to every
   running kernel — precisely the conflation ADR-0015 dec 3's audience-binding exists to prevent.

   > **rev 1 (2026-07-16): "authorised by the platform session" means *derived from* one, not
   > *presented as* one — and the audience is what makes decision 4 enforceable at all.**
   >
   > **The plane.** This record introduces a fifth capability audience, for uploads, of the same
   > opaque, single-tenant, centrally-revocable form as the others (ADR-0015 dec 1, 3) — the pattern
   > every plane before it has extended rather than replaced. It is **not read-only**: it authorises
   > writing one artifact, and its boundary is the tenant namespace its mediator enforces, exactly as
   > ADR-0015 dec 1 rev 1 describes for a writing plane.
   >
   > **Why an audience of its own, restated because rev 0 undersold it.** Rev 0 gave the reason as
   > "reusing the kernel's audience would hand the upload surface to every running kernel", which is
   > true and is the smaller half. The larger half is that **decision 4 refuses executable
   > serialisation on this path and deliberately leaves the notebook path alone.** Two populations
   > therefore reach the same mediator under different rules, and the audience is the only fact that
   > distinguishes them. Collapse the two and there is no third option: either kernels inherit a
   > refusal this record never decided for them, or uploads escape the refusal that is the
   > precondition for admitting them at all. The audience is not a lock on this door — it is what
   > makes the door a different door.
   >
   > **The minter, and why not the mediator.** The handle is **demand-minted** (ADR-0015 dec 4 rev 1)
   > by the **serving side** — the component that already establishes tenant from a verified session
   > for its own reasons, and that is already this artifact's admission authority at the registration
   > gate (decisions 5 and 6). An upload is the preamble to registration, so the authority that will
   > judge the artifact is the one that authorises it to arrive; the question "who may issue this"
   > gets one answer rather than a convention.
   >
   > The mediator holding the artifact-store credential **must not verify the session itself**. The
   > platform's session signature is symmetric, so a component that can verify a session can forge
   > one — for any user, in any tenant. Today that mediator's compromise costs an object-store
   > principal and a store of revocable, single-tenant handles; teaching it to verify sessions would
   > make its compromise cost every tenant's identity, permanently, and would make ADR-0015 dec 7's
   > bound on what a store-reader can lose a fiction. It resolves an opaque handle instead, which is
   > the platform's one mediation pattern and the whole reason capabilities exist: **hold an
   > operation, not a credential.**

4. **Only artifacts that can be loaded without executing author-supplied code are accepted; an
   artifact carrying executable object-serialisation is refused at the gate.** The path additionally
   requires that the loader never fall back to such a format, which it does by default
   (`MLFLOW_ALLOW_PICKLE_DESERIALIZATION` defaults true).

   > **rev 1 (2026-07-16): the rule is generic, and the framework names are not part of it.** Rev 0
   > read "the supported serialisations are those ADR-0027 dec 3 already governs — `skops` for
   > scikit-learn, the native booster for xgboost", which invites a closed list of *frameworks* to be
   > written into the refusal. That would be domain-specific knowledge inserted into a core path, and
   > ADR-0008 dec 1 forbids exactly that; ADR-0027 was careful for the same reason, calling its own
   > list "an instance, not the rule" and its rule "deliberately generic — it needs no amendment when
   > torch arrives". A refusal that enumerates frameworks needs amending for every new one, and would
   > refuse a safe format nobody had thought of yet.
   >
   > The rule is therefore a property of the **serialisation**, not of the framework: *does loading
   > this require deserialising author-supplied objects?* Executable object-serialisation
   > (pickle and its relatives) is refused whether it is declared or merely present, and whatever
   > flavor carries it. Today's safe formats are what the platform's own supported set happens to
   > use; they are an instance of the rule passing, never the rule itself, and this record does not
   > name them.
   >
   > **What the gate is not asking** is whether this deployment can *serve* the artifact. An open
   > framework set cannot be judged by a closed list (ADR-0027 dec 2), and the answer already lives
   > in a registry that is discovered rather than hand-kept. A safe-but-unservable artifact passes
   > this gate on structure and is refused, with a reason, at the one that knows — decisions 5 and 6.

   This resolves, for this path only, what ADR-0027 deferred as "the trust half of this boundary".
   It is deferred there and urgent here, and the difference is entirely provenance:

   > For a kernel-authored artifact, a pickle grants its author code execution in an environment
   > where they already had code execution — their own sandboxed kernel (ADR-0011 dec 2/7). It buys
   > an attacker nothing they did not already hold, which is why refusing it was hardening. For an
   > uploaded artifact, the author has no foothold at all. A pickle would be the platform *granting*
   > arbitrary code execution inside the serving environment, on the trusted side of the boundary,
   > under that environment's credentials, to anyone able to authenticate a session.

   Same format, same loader, same line of code — different blast radius, because provenance changed.
   Refusing pickle is therefore not hardening on this path. It is the **precondition for admitting
   uploads at all**, and if it cannot be enforced, the rest of this record does not apply.

5. **The artifact's output semantics are declared explicitly by the uploader, and the declaration is
   judged at the serving side's registration gate — or the model is refused there.**
   ADR-0028 dec 1 made declared semantics a first-class fact and dec 2 forbade inferring them from a
   prediction's value or dtype. The kernel emits that declaration today; an external author has no
   kernel to emit it from, so the declaration becomes a required input. There is no default and no
   fallback: a model whose declared semantics is absent, is outside the vocabulary ADR-0028 governs,
   or names something no registered detector implements for that artifact's flavor, is refused —
   exactly as a guess would be.

   > **rev 1 (2026-07-16): the moment.** Rev 0 said "at upload" and left the enforcement point to be
   > inferred. The declaration is *carried* with the artifact from the moment it is uploaded, but it
   > is *judged* at the **registration gate** — the same gate ADR-0027 dec 5 already operates, which
   > refuses an artifact this environment was never able to serve. It is judged there and not at the
   > gateway because only the serving side holds the vocabulary and the live detector registry, and
   > that registry is *discovered*, never hand-kept — a copy would be wrong the first time an
   > operator installed an adapter. It is judged there and not at the deployment gate because a model
   > whose output means nothing here was never registrable, and deferring the question would leave it
   > sitting in the registry in a state the registry has no way to describe.
   >
   > That an uploaded artifact can therefore exist in the store before it is admitted is not a new
   > weakness: it is the window a kernel-authored model already occupies between logging its bytes
   > and being registered. The standard is unchanged; only the door is new.

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

   > **rev 1 (2026-07-16):** this **extends the deployment gate the serving side already operates** —
   > the one that refuses a model which was servable when registered and whose ground moved
   > afterwards — rather than adding a third gate. Rev 0's "before it may be deployed" already named
   > that moment; rev 1 says so plainly, because a parallel load-and-score path built beside the
   > existing gate would be a second answer to a question that already has one.

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
- **B. A dedicated upload service, or the serving side, holding its own object-store principal.**
  Simpler to build — the gateway is not shaped for bulk ingestion, and the serving side already carries
  the tenant name rule and already addresses the tracking server as trusted code. Rejected on decision
  2's narrower ground: either would require an object-store write credential it does not hold today,
  establishing a second privileged writer to the store in which every tenant's models reside. The
  convenience is immediate; the additional authority is permanent.
- **C. Refuse external models; require that training occur in the kernel.** Coherent, and the position
  the architecture holds by default. Rejected because it answers a real requirement with an
  environment that cannot meet it: the cold layer exists so that history outlives the hot store, and
  training over that horizon is beyond what an embedded kernel is intended to carry.
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
  already does — the gateway's, and the trusted read-path's — and declines to treat that as an
  argument. Whether the two should converge, and which owns the rule, is a live question this record
  surfaces without settling. ADR-0019 dec 5 reads as though the answer is one; the realisation has
  been two since the trusted read-path existed.

## References

- ADR-0027 — the declare/satisfy-or-refuse contract this record extends; dec 3 (supported flavor set,
  serialisation), dec 5 (both checks required, empirical is the authority), and the deferred "trust
  half" that decision 4 resolves for this path.
- ADR-0019 — dec 1 (the "only" that governs the *kernel's* credentials, and which this record does
  **not** except — see Context), dec 3 (tracking-capability audience), dec 5 (the gateway's
  tenant-namespace enforcement, whose rule the trusted read-path already re-derives — rev 1), dec 6
  (the privileged artifact-store principal — decision 2's actual ground), dec 7 (the trusted-side
  scoped API/UI that shows the gateway was never meant to be the only speaker to MLflow).
- ADR-0028 — dec 1/2 (declared semantics; never guess), dec 7 (the out-of-process record decision 9
  refuses to pre-empt).
- ADR-0025 — the cold layer whose durable history motivates off-platform training.
- ADR-0011 — dec 2/7 (the untrusted, sandboxed kernel whose bound decision 4 turns on).
- ADR-0021 — the drift lane and the training baseline an uploaded model has no run to carry.
- ADR-0015 — dec 3, audience-bound capabilities.
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
