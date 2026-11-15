<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0026 — Release model: one trunk and a gate that runs the system

- **Status:** Accepted
- **Date:** 2026-07-13; changed 2026-11-14

## Context

1. Checks that reason about the dependency graph cannot see a dependency that resolves but fails on
   start, or a component that starts on fresh state but fails on state from the version it upgrades
   from.
2. An integration branch is a place to run a test, not a test.
3. The long-lived environment sees accumulated state, real certificates, real name resolution and
   the public edges; its runs after merge keep finding failures that pass every check, and until now
   they were scripts on that host, unreviewed and not repeatable.
4. A build input fetched by address comes from a public repository that refuses some runners
   outright, so a check can fail for the runner rather than the change.

## Decision

1. **One trunk.** The main branch is the only long-lived branch; feature branches merge by pull
   request when the required checks pass.
   Rests on: 2.
2. **The main branch means every required check passed, and at least one starts the system.** It
   does not mean box-validated.
   Rests on: 1.
3. **A required whole-stack gate runs the system and judges liveness over an interval.** It asks
   whether a service is still restarting after convergence, not whether it ever restarted; the
   monitoring stack and stream engines are outside it.
   Rests on: 1.
4. **The gate exercises the upgrade path.** Stateful components start on synthesised state from the
   commit the change merges onto, holding no users or credentials.
   Rests on: 1.
5. **Every image must start, not merely build.** Each image loads the code it serves in its build
   check.
   Rests on: 1.
6. **The box run is post-merge discovery, not a merge gate.** What it finds becomes a CI check where
   CI can reproduce it, and an end-to-end journey (decision 10) where it cannot.
   Rests on: 3.
7. **The newest image tag tracks the main branch, and dependency updates target it.** A patch
   reaches the newest tag through one green pipeline.
   Rests on: none.
8. **A commit is box-validated when it was deployed on the long-lived environment by upgrade from
   the last box-validated commit, and the repository's end-to-end suite passed against it through
   the public edges.** Fresh state reproduces what the whole-stack gate already sees; the failures
   came from state that only accumulates.
   Rests on: 3.
9. **Box validation is a status on the commit, and a versioned release is cut only from a commit
   that carries it.** The release names images already built and scanned for that commit; nothing is
   rebuilt. The main branch keeps the meaning of decision 2.
   Rests on: 3.
10. **The suite holds one journey for each kind of state or infrastructure CI does not reproduce,
    starting with those the box found failures in.** A tenant's life through deletion, a
    capability's use and its record, a bound configuration, a device's reading through the
    certificate edge, and inference on a deployed model.
    Rests on: 3.
11. **A check of a service's use of a store or broker runs the service's own driver and client
    against a real server of it.** Another driver binds parameters another way, and an in-process
    stand-in has no timeouts, limits or coordinator, so each passes what the service then fails.
    Rests on: 1.
12. **A build input fetched by address is pinned by a content digest the repository holds, and has a
    second source.** A required check then fails for the change, not because one host refused the
    runner. A digest the repository holds fixes the bytes to those checked when it was pinned; a
    digest the serving host publishes says only that the transfer was whole.
    Rests on: 4.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | An integration branch before the trunk | Runs no test the trunk lacks; delays patches, makes the newest tag ambiguous |
| 3 | Any restart as the failure signal | Fails every cold start that races its dependencies, so the gate is routinely red and ignored |
| 3 | Zero restarts with longer start-up waits | Tuned to one runner's speed |
| 3 | Only the targeted live integration proofs | A service outside every slice has no proof |
| 4 | Synthesised state from the last versioned release | A change would fail on an earlier merge's break; the upgrade across merges is decision 8's |
| 6 | Tagging box-validated commits | Records a validation, blocks nothing |
| 6 | Box validation as a merge gate | The run needs the long-lived environment and an upgrade; patches would wait on it |
| 8 | The suite against a freshly installed environment | Reproduces the whole-stack gate; every failure the box found needed accumulated state |
| 8 | Scripts kept on the long-lived host | Unreviewed, unrepeatable, and nothing a status can name |
| 9 | Recording the validation only | Blocks nothing an operator deploys |
| 9 | A tag in the repository as the record | A tag moves silently and carries no result; release automation reads statuses |
| 9 | Rebuilding images for the release | The released bytes would not be the ones validated |
| 10 | One journey per decision record | Failures sit where decisions meet, and the suite would grow with every record |
| 11 | A convenient driver for the database proofs | It cast what the service's driver refuses, and passed a write the service could not make |
| 11 | An in-process stand-in for the shared store | It has no client timeout, and passed a read the service's client timed out on |
| 11 | A stand-in for the broker client | It has no group coordinator, so commit, rewind and redelivery behave as the stand-in was written, not as the broker does |
| 12 | The digest the serving host publishes beside each input | Proves the transfer, not the content, and fails with the host |
| 12 | Rerunning a check the host refused | A required check that is red for the runner is rerun until it is ignored, and a rerun can hide a real failure |
| 12 | A cache of verified inputs on the runner | A cold or evicted cache on a refused runner still fails, and the long-lived environment builds without it |
| 12 | The inputs committed to the repository | Grows the repository by every version of every input |

## Consequences

**Positive**
- Start-time and upgrade failures are caught by a machine before merge.
- The main branch and the newest image mean one written thing; patches keep a short path.
- A required check is not red because one host refused one runner.

**Negative**
- The whole-stack gate is the most expensive check and slows as the system grows.
- A release waits for a run on the long-lived environment, which one host provides.
- The environment builds the commit's images itself, so a release publishes the main pipeline's
  build of the validated commit: the same sources and pins, not the same bytes.
- The suite runs with a platform superuser and the environment's internals, so it runs only where
  both are the test environment's own.
- Synthesised state covers schema upgrades, not accumulated data, and only the latest upgrade step.
- Each upgrade path's from-version must move with the component's major.
- A break confined to a component outside the gate is caught only by its own build.
- Every version change of a fetched input needs its new digests.
- The second source is another host the build depends on, trusted only through the digest.

## Deferred

| Open question | Settled by |
|---|---|
| Upgrade paths for the database, tracking backend and capability store | The first upgrade failure in one of them |
| Database major-version upgrades | Its own decision, when one is planned |
| The long-lived environment running the published images | The first difference between its build and the published one that a run would have caught |

## References

- [CI and required checks](../docs/operations/ci.md): manual of decisions 1-12.
