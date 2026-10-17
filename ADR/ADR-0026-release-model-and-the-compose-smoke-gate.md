<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0026 — Release model: one trunk and a gate that runs the system

- **Status:** Accepted
- **Date:** 2026-07-13; changed 2026-10-17
- **Related:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (the images certified), [ADR-0017](ADR-0017-database-tls-and-authentication.md) and [ADR-0018](ADR-0018-notebook-hub-spawner-portability.md) (their "validated on the box" language)

## Context

Checks that reason about the dependency graph cannot see a dependency that resolves but fails on start,
or a component that starts on fresh state but fails on state from the version it upgrades from. An
integration branch is a place to run a test, not a test. The box run sees accumulated state but is
manual, slow and irreproducible, and a gate that depends on a person remembering it is not a gate.

## Decision

1. **One trunk.** The main branch is the only long-lived branch; feature branches merge by pull request
   when the required checks pass.
2. **The main branch means every required check passed, and at least one starts the system.** It does
   not mean box-validated.
3. **A required whole-stack gate runs the system and judges liveness over an interval.** It asks whether
   a service is still restarting after convergence, not whether it ever restarted; the monitoring stack
   and stream engines are outside it.
4. **The gate exercises the upgrade path.** Stateful components start on synthesised state from the
   version upgraded from, holding no users or credentials.
5. **Every image must start, not merely build.** Each image loads the code it serves in its build check.
6. **The box run is post-merge discovery, not a merge gate.** What it finds becomes a CI check.
7. **The newest image tag tracks the main branch, and dependency updates target it.** A patch reaches a
   release through one green pipeline.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | An integration branch before the trunk | Runs no test the trunk lacks; delays patches, makes the newest tag ambiguous |
| 3 | Any restart as the failure signal | Fails every cold start that races its dependencies, so the gate is routinely red and ignored |
| 3 | Zero restarts with longer start-up waits | Tuned to one runner's speed |
| 3 | Only the targeted live integration proofs | A service outside every slice has no proof |
| 6 | A box-validated status gating releases | Deferred until the box finds what synthesised state cannot reproduce |
| 6 | Tagging box-validated commits | Records a validation, blocks nothing |

## Consequences

**Positive**
- Start-time and upgrade failures are caught by a machine before merge.
- The main branch and the newest image mean one written thing; patches keep a short path.

**Negative**
- The whole-stack gate is the most expensive check and slows as the system grows.
- Synthesised state covers schema upgrades, not accumulated data, and only the latest upgrade step.
- Each upgrade path's from-version must move with the component's major.
- A break confined to a component outside the gate is caught only by its own build.

## Deferred

| Open question | Settled by |
|---|---|
| A box-validated status gating releases | The box repeatedly finding failures CI cannot reproduce |
| Upgrade paths for the database, tracking backend and capability store | The first upgrade failure in one of them |
| Database major-version upgrades | Its own decision, when one is planned |

## References

- `.github/workflows/compose-smoke.yml` (decisions 3–4)
- `.github/workflows/images.yml` (decisions 5, 7)
- `.github/dependabot.yml` (decision 7)
