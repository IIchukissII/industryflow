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
- **Companions:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (the images this gate certifies, and the digest pinning that consumes them), [ADR-0018](ADR-0018-notebook-hub-spawner-portability.md) (the deployment profile a whole-stack gate must stand up)
- **Related:** [ADR-0017](ADR-0017-database-tls-and-authentication.md) (whose "validated live on the box" language this ADR finally gives a definition to)

## Context and problem

The project has a branching model, a release flow, and a phrase — *"box-validated"* — that other ADRs
lean on. **None of the three is written down.** They are implemented by default, in workflow YAML, and
answerable to no decision. ADR-0000 decision 1 says a decision that constrains downstream artifacts is
captured *before* it is propagated into code; here the artifacts came first and the decision never came.

What those artifacts silently imply today: `main` means "the required checks were green"; `:latest` —
the tag a self-hoster pulls — means the same and nothing more; Dependabot opens against `main`.

**Two failures in one sprint showed what that meaning is worth.** Both passed every gate:

- **MLflow does not run on Python 3.14.** It imports a stdlib name 3.14 removed. mlflow is pure-python,
  so its whole dependency closure *resolves perfectly* — no lock, wheel check or resolver can see it.
  Only starting the server finds it.
- **JupyterHub 5 will not boot on a 4.x state database.** It does not warn; it exits, and the container
  crash-loops. Every check was green, the image built, its config parsed — and an operator upgrading an
  existing hub would have got a dead one. Nothing in CI started the hub, so nothing in CI could know.

The tempting conclusion is that `main` needs a staging branch in front of it: feature → `dev` → a human
validates on the box → `main`, so `main` means *box-validated* rather than merely *CI-green*. That
conclusion is wrong, and why it is wrong is the substance of this ADR:

> **The branch is not the gate. The test is the gate.**

A `dev` branch would have caught neither failure. It is a *place* to run a test, not a test. Both bugs
were found by starting the thing — one by a live proof that already existed, one by a human typing
`docker compose up`. What was missing was never a branch. It was a check that runs the artifact instead
of describing it.

## Decision drivers

- **A resolve is not a run.** Every gate the project had reasoned about the dependency graph: do the
  pins satisfy each other, do the wheels exist, does it import. Both failures cleared all of that and
  died on start.
- **The dangerous state is the state that already exists.** A fresh stack would have started JupyterHub 5
  happily — it creates a current-version schema and never meets an old one. The upgrade path is the path
  that breaks, and it was the one nothing covered.
- **`main` and `:latest` are contracts.** If they can drift apart, or mean two different things depending
  on which branch you look at, they are not contracts a self-hoster can act on.
- **A gate a human must remember is not a gate.** Box validation is valuable, but it is manual, slow, and
  runs against one irreproducible machine. What defines `main` has to be automatic.
- **Dependency patches must not queue behind a person.** Dependabot is the security patch path across four
  ecosystems; a model that parks its PRs behind a manual step trades a real recurring property for a
  hypothetical one.

## Decision

1. **One trunk.** `main` is the only long-lived branch; no integration branch is introduced. Feature
   branches open a pull request against `main` and merge when the required checks pass.

2. **`main` means: every required check passed — and at least one of them starts the system.** This is
   the sentence the project never wrote. `main` does **not** mean "box-validated" (decision 6).

3. **A required whole-stack gate runs the system and observes that it stays up.** Building an image and
   importing its modules are not evidence that it runs; this gate supplies that evidence, and a service
   that cannot stay up fails the merge.

   **Liveness is judged over an interval, not at an instant.** A crash-looping process is "running"
   whenever you happen to look at it — which is exactly how the JupyterHub failure would pass a
   point-in-time check. Equally, a service that restarts a few times while waiting for a database to
   accept connections and then settles has not failed. So the property the gate must establish is
   *convergence and then stability*, and the question it must answer is **not "did it restart?" but "is
   it still restarting?"**

4. **The gate exercises the upgrade path: stateful components are started against state written by the
   version being upgraded from.** A gate that only ever meets empty volumes cannot see an upgrade
   failure, because on empty volumes there is nothing to upgrade. This is the decision that would have
   caught JupyterHub, and it is the one most easily lost — an "upgrade test" that quietly runs on a fresh
   volume tests nothing while appearing to test everything.

   The state it starts from is **synthesised, never copied from a live deployment.** A real deployment's
   database carries users and credentials, and a fixture holding credentials is not a fixture.

5. **An image must start, not merely build.** Each image is required to load the code it exists to serve.
   This is cheap, and it is the entire test for the "resolves cleanly, dies on import" class.

6. **The box run stays, and is deliberately NOT a merge gate.** A human validation on the compose box is
   the deepest proof the project has, and the only thing that exercises *accumulated* state — real data,
   configuration drift, an operator's actual volumes. That is exactly why it cannot gate a merge: it is
   manual, slow, single-instance, and irreproducible. Its role is **post-merge deep validation and
   discovery** — it finds what CI cannot, and its findings then *become* CI checks. (This ADR is the
   output of one such run.) Recording this gives the phrase a definition it never had.

7. **`:latest` tracks `main`, and Dependabot targets `main`.** Both follow from decisions 1 and 2, and are
   stated only because they are the artifacts a `dev` model would have forced to change: `:latest` is the
   most recent `main` commit whose checks passed, and a security patch's path to a release is never longer
   than one green pipeline.

**Scope of decision 3, stated rather than implied.** The gate covers the application services and their
infrastructure. Components whose cost outruns their risk on a CI runner — the monitoring stack, the Spark
JVMs — are out of scope, and a break confined to them is caught only by their own build. That is a known
limit of this decision, not an oversight; the concrete inclusion list belongs to the workflow, not here.

## Alternatives considered

**A. A long-lived `dev` integration branch (feature → `dev` → box → `main`).** *Rejected.* It answers a
question nobody asked: neither failure was caused by insufficient staging, and `dev` runs no test that
`main` does not. It buys the *appearance* of a gate while the real gate — starting the system — still has
to be written either way. Against that it charges: Dependabot patches waiting on a human box run;
`:latest` ambiguous between two branch heads; image publishing doubled; and a growing `dev`↔`main` delta
that converts many small reviewed merges into one large unreviewed one. **If the checks of decisions 3-5
exist, `dev` adds nothing; if they do not, `dev` catches nothing.**

**B. A `box-validated` commit status gating the *release* rather than the merge.** *Deferred, not
rejected.* The box posts a status against the merged commit, and `:latest` and the digest overlay are
gated on it — keeping one trunk while letting `main` mean CI-green and `:latest` mean box-validated. It
is deferred because it is only worth its complexity if the box's live state proves to hold failures that
decision 4's synthesised state cannot reproduce. Decisions 3-5 are the cheaper experiment and come first.

**C. Tagging box-validated commits.** *Rejected as the primary instrument.* It records that a validation
happened but enforces nothing — a release cut from an untagged `main` is not blocked. It is documentation
wearing a gate's clothes.

**D. Rely on the targeted live integration proofs already in CI.** *Rejected.* Those proofs are real and
earn their keep — one of them is what caught MLflow on 3.14. But each stands up only the slice its own ADR
needed, so a service outside every slice (the hub) had no proof at all. The gap was not that they were
weak; it was that nothing covered the system as a whole.

## Consequences

### Positive

- The two failure classes are now caught by a machine rather than a person: "resolves cleanly, dies on
  import" by decision 5, and "starts fine when new, dies when upgraded" by decisions 3-4.
- `main` and `:latest` mean exactly one thing, the same thing, and it is written down.
- Dependency patches keep their short path to a release.
- The box run gets a defined job — post-merge discovery against accumulated state — instead of being an
  undefined phrase other ADRs lean on.

### Negative

- **The whole-stack gate is the most expensive check in CI** and will slow as the system grows. It is
  bounded by keeping it a *smoke*: converge, observe, probe. Deep functional proof stays in the targeted
  integration workflows that already own it.
- **A synthesised upgrade path is only as good as the state it starts from.** It covers the schema class,
  because that is the class that bit. It does not reproduce an operator's accumulated data and never
  will — which is why decision 6 keeps the box run rather than pretending CI replaced it.
- **The starting state must be maintained.** When a component's major moves, the version it is upgraded
  *from* must move with it, or the upgrade test quietly starts testing nothing — the same trap decision 4
  exists to name.
- Only the most recent upgrade path is proven. A deployment that skips two majors is not covered.

## Deferred decisions

- **The `box-validated` release status (Alternative B).** Revisit if the box keeps finding failures a
  synthesised CI stack provably cannot reproduce.
- **Which stateful components earn an upgrade path.** Today: the one that broke. Whether the database, the
  tracking backend and the capability store each get one is left to when one of them actually breaks on
  upgrade.
- **Database major upgrades.** A data migration with real dump/restore risk; out of scope here and owed
  its own decision.

## References

- ADR-0000 — the discipline that says this decision had to be recorded before it was implemented, which is
  why this file exists.
- ADR-0009 — the image build/publish flow that decisions 5 and 7 constrain.
- ADR-0017, ADR-0018 — the ADRs whose "validated on the live box" language decision 6 defines.
- Implementation: `.github/workflows/compose-smoke.yml` (decisions 3-4) and the per-image smoke in
  `.github/workflows/images.yml` (decision 5). The mechanism lives there deliberately; this ADR fixes the
  properties those jobs must establish, not the commands by which they establish them.
- Incidents (2026-07-13): mlflow on Python 3.14 (#222); JupyterHub 5 on a 4.x state database (#231).
