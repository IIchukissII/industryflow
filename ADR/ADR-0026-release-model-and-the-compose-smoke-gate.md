<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0026 — Release model: one trunk, and what `main` is allowed to mean

- **ID:** ADR-0026
- **Status:** Accepted
- **Date:** 2026-07-13
- **Project:** IndustryFlow
- **Parent:** ADR-0000 (decision records and the single-source-of-truth discipline)
- **Companions:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (the images this gate certifies, and the digest pinning that consumes them), [ADR-0018](ADR-0018-notebook-hub-spawner-portability.md) (the compose profile the gate stands up)
- **Related:** [ADR-0017](ADR-0017-database-tls-and-authentication.md) (the staged rollouts whose "validated live" language this ADR finally gives a definition to)

## Context and problem

The project has a branching model, a release flow, and a phrase — *"box-validated"* — that several ADRs
lean on. **None of the three is written down anywhere.** They are implemented by default, in workflow
YAML, and answerable to no decision. ADR-0000 decision 1 says a decision that constrains downstream
artifacts is captured *before* it is propagated into code; here the artifacts came first and the
decision never came at all.

What the artifacts currently imply, without ever saying so:

- **`main` means "the required CI checks were green."** Feature branches PR into `main`; five gates
  guard the merge (`unit-tests-gate`, `cold-layer-integration-gate`, `db-tenant-isolation-gate`,
  `tracking-gateway-integration-gate`, `deps-resolve`).
- **`images.yml` publishes `:latest` + `:sha-<short>` to GHCR on every push to `main`** — so `:latest`,
  the tag a self-hoster pulls, *already* means "CI-green", and nothing more.
- **Dependabot opens against `main`.**

**Two failures in one sprint showed what that meaning is worth.** Both survived every gate:

- **MLflow does not run on Python 3.14.** `mlflow/assistant/skill_installer.py` imports
  `importlib.abc.Traversable`, which 3.14 removed. mlflow is pure-python, so its whole 95-package
  closure *resolves perfectly* — no wheel check, lock regeneration, or dependency gate can see it. Only
  starting the server finds it. The live tracking-gateway proof did, because it happens to drive a real
  MLflow write.
- **JupyterHub 5 will not boot on a 4.x state database.** It does not warn; it exits, and the container
  crash-loops. Every check was green, the image built, its config parsed, and an operator upgrading an
  existing hub would have got a dead one. **Nothing in CI starts the hub**, so nothing in CI could know.
  A human box run found it.

The tempting conclusion is that `main` needs a staging branch in front of it — feature → `dev` →
(a human validates on the box) → `main`, so `main` means *box-validated* rather than merely *CI-green*.
That conclusion is wrong, and the reason it is wrong is the whole point of this ADR:

> **The branch is not the gate. The test is the gate.**

A `dev` branch would not have caught either failure. It is a place to run a test, not a test. Neither
bug was found by *staging* the change; both were found by *starting the thing* — one by a live proof
that already exists in CI, one by a human who typed `docker compose up`. What was missing was never a
branch. It was a check that starts the stack, and — for the JupyterHub class — a check that starts it
**against state that already exists**, because on a fresh volume that bug is invisible.

## Decision drivers

- **A resolve is not a run.** Every gate the project had reasoned about *dependency graphs*: do the
  pins satisfy each other, do the wheels exist, does it import. Both failures cleared all of that and
  died on start. The gate has to execute the artifact, not describe it.
- **The dangerous state is the state that already exists.** A fresh `docker compose up` on empty volumes
  would have started JupyterHub 5 happily — it creates a 5.x schema and never meets a 4.x one. The
  upgrade path is the path that breaks, and it is the one no test covered.
- **`main` should mean one thing, and the tag should mean the same thing.** `:latest` is a contract with
  whoever pulls it. If `main` and `:latest` can drift apart, or if `:latest` could mean either of two
  branches' heads, the contract is not one a self-hoster can act on.
- **A gate a human has to remember is not a gate.** Box validation is valuable and must continue, but it
  is manual, slow, and runs against one irreproducible machine. Anything load-bearing enough to define
  what `main` means has to be automatic.
- **Dependency patches must not queue behind a human.** Dependabot is the project's security patch path
  across four ecosystems. Any model that parks its PRs behind a manual step trades a real, recurring
  security property for a hypothetical integration one.

## Decision

1. **One trunk. `main` is the only long-lived branch, and no integration branch is introduced.** Feature
   branches open a pull request against `main` and merge when the required checks are green. A
   long-lived `dev` branch is rejected (Alternative A): it would not have caught either failure that
   motivated it, while introducing costs that are certain — Dependabot PRs queueing behind a manual box
   run, `:latest` becoming ambiguous between two branch heads, a doubled image-publish, and a growing
   `dev`↔`main` delta whose eventual merge is the one merge nobody reviewed.

2. **`main` means: every required check passed, and one of those checks starts the stack.** This is the
   sentence this ADR exists to write, and the substance of it is decision 3. What `main` does *not* mean
   is "box-validated" (decision 6).

3. **A `compose-smoke` gate is added, and it is required.** It stands the real Compose stack up —
   the application services and the notebooks profile, with the infrastructure they depend on — and
   asserts that **every container converges to healthy and none is restarting**, plus a small set of
   endpoint probes. A crash-looping container fails the merge. A non-zero restart count *is* the signal:
   a container that dies and respawns is "running" when you look at it, which is exactly how JupyterHub 5
   would have slipped past a naive check.

   Its scope is stated rather than implied: the monitoring stack (Prometheus/Grafana/Loki/exporters) and
   the Spark JVMs are **out of scope**, because a CI runner cannot converge the whole 31-container stack
   and neither sits on the failure path this gate exists for — a Python dependency that kills a service
   on start. A break confined to a Spark image is therefore still caught only by its own build, and that
   is a known limit, not an oversight.

4. **The `compose-smoke` gate seeds pre-existing state; it does not run on empty volumes.** Specifically,
   the JupyterHub state database is seeded at the **previous major's schema** before the stack starts,
   because that is the only condition under which the JupyterHub-5 crash-loop reproduces at all. The
   seed is *generated* in CI by the old JupyterHub (`jupyterhub.orm.new_session_factory` under the prior
   pin), never a copy of a real hub's database — a real one carries users and API tokens, and a fixture
   with credentials in it is not a fixture. **An upgrade test on a fresh volume is not an upgrade test.**

5. **Every image must start, not merely build.** `images.yml` gains a per-image smoke step that runs the
   built image and imports the module the container actually serves — for the tracking server,
   `mlflow.server.fastapi_app`, the exact import that dies on Python 3.14. This is nearly free, runs on
   every PR, and catches the whole class of "resolves cleanly, dies on start" without any stack at all.

6. **The box run stays, and is deliberately NOT a merge gate.** A human validation on the compose box
   remains the deepest proof the project has, and it is the only thing that exercises *accumulated*
   state — months of real data, configuration drift, an actual operator's volumes. That is precisely why
   it cannot gate a merge: it is manual, slow, single-instance, and irreproducible. Its role is
   **post-merge deep validation and discovery** — the thing that finds what CI could not, and whose
   findings then become CI checks (this ADR is itself the output of one such run). Recording it here
   gives it a definition it never had.

7. **`:latest` tracks `main`, and Dependabot targets `main`.** Both follow from decisions 1 and 2 and are
   restated only because they are the artifacts that would have had to change under a `dev` model:
   `:latest` is the most recent `main` commit whose checks — now including `compose-smoke` — passed, and
   Dependabot keeps opening against `main` so a security patch's path to a release is never longer than
   one green pipeline.

## Alternatives considered

**A. A long-lived `dev` integration branch (feature → `dev` → box → `main`).** *Rejected.* It answers a
question nobody asked: neither failure was caused by insufficient staging, and a `dev` branch runs no
test that `main` does not. It buys the *appearance* of a gate while the actual gate — starting the
stack — still has to be written either way. Against that it charges: Dependabot patches waiting on a
human box run; `:latest` ambiguous between `dev`'s head and `main`'s; image publishing doubled across
18 images; and `dev`↔`main` drift, which converts many small reviewed merges into one large unreviewed
one. If the tests of decisions 3-5 exist, `dev` adds nothing; if they do not, `dev` catches nothing.

**B. A `box-validated` commit status gating the release rather than the merge.** *Deferred, not
rejected.* The box posts a status to the merged SHA via the API, and `:latest`/the digest overlay are
gated on it. This is a coherent way to make `main` mean CI-green while `:latest` means box-validated,
and it keeps one trunk. It is deferred because it is only worth its complexity if the box's live state
proves to hold failures that decision 4's seeding cannot reproduce. Decisions 3-5 are the cheaper
experiment and come first; if they prove sufficient, this is never needed.

**C. Tagging box-validated commits.** *Rejected as the primary instrument.* It records that a validation
happened but enforces nothing — a release cut from an untagged `main` is not blocked, so the tag is
documentation wearing a gate's clothes.

**D. Keep the status quo and rely on the live integration proofs already in CI.** *Rejected.* Those
proofs (cold-layer, tracking-gateway, db-tenant-isolation) are real and they earn their keep — the
tracking-gateway one is what caught MLflow on 3.14. But they are *targeted*: each stands up the slice
its own ADR needed, and a service outside those slices (the hub) had no proof at all. The gap was not
that the proofs were weak; it was that nothing covered the stack as a whole.

## Consequences

### Positive

- The two failure classes this sprint hit are now caught by CI rather than by a person: "resolves
  cleanly, dies on import" by decision 5, and "starts fine when new, crash-loops when upgraded" by
  decisions 3-4.
- `main` and `:latest` mean exactly one thing, and the same thing, and it is written down — so a
  self-hoster pulling `:latest` and an operator reading this file agree about what they are getting.
- Dependency patches keep their short path to a release; the security property Dependabot exists for is
  not traded away for an integration property.
- The box run gets a definition and a job — post-merge deep validation against accumulated state — rather
  than being an undefined phrase that several ADRs lean on.

### Negative

- **The full-stack smoke is the most expensive check in CI**, and it will get slower as the stack grows.
  It is bounded by keeping it a *smoke* — converge and probe, not a functional suite; the deep functional
  proofs stay in the targeted integration workflows they already live in.
- **A seeded upgrade path is only as good as the seed.** Decision 4 covers the schema-migration class,
  because that is the class that bit. It does not reproduce an operator's months of accumulated data,
  and it never will — which is exactly why decision 6 keeps the box run rather than pretending CI has
  replaced it.
- Seeds must be maintained: each time a component's schema major moves, the seed's baseline moves with
  it, or the upgrade test quietly starts testing nothing.
- The gate can only prove the *last* upgrade path (previous major → current). A deployment skipping two
  majors is not covered.

## Deferred decisions

- **The `box-validated` commit status (Alternative B).** Revisit if the box keeps finding failures that
  a seeded CI stack provably cannot reproduce.
- **How far back the upgrade seed reaches.** Today: the immediately previous major of the component that
  broke. Whether every stateful component (TimescaleDB, MLflow's backend, the capability store) earns a
  seeded upgrade path is left to when one of them actually breaks on upgrade.
- **Postgres/TimescaleDB major upgrades.** A data migration with real dump/restore risk; out of scope
  here and owed its own decision.

## References

- ADR-0000 — the discipline that says this decision had to be recorded before it was implemented, which
  is the reason this file exists at all.
- ADR-0009 — the image build/publish flow (`images.yml`, `:latest` + `:sha-<short>`, digest pinning) that
  decisions 5 and 7 constrain.
- ADR-0017, ADR-0018 — the ADRs whose "validated on the live box" language decision 6 finally defines.
- Incident record (2026-07-13): mlflow on Python 3.14 (issue #222), JupyterHub 5 on a 4.x state database
  (PR #231) — the two failures whose shape this ADR is built around.
