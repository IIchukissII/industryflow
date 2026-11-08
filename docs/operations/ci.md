<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# CI and required checks

The GitHub Actions workflows, their gate jobs, and the required checks on `main`
([ADR-0026](../../ADR/ADR-0026-release-model.md)).

## Gate convention

| Rule | Detail |
|---|---|
| Triggers | Every workflow runs on `pull_request` and on `push` to `main` |
| One gate per workflow | Each workflow ends in a `<name>-gate` job with `if: always()`, so it always reports a status |
| Path-filtered workflows | A `changes` job (`dorny/paths-filter`) decides whether the workflow's paths changed. Unchanged: the work is skipped and the gate passes. Changed: the gate fails when any gated job failed or was cancelled, or when `changes` itself did not succeed |
| Unfiltered workflows | `compose-smoke.yml` and `config-guards.yml` have no `changes` job; their jobs run on every PR and the gate passes only when all of them succeeded |

## Required checks on `main`

| Gate | Workflow file | Gated jobs |
|---|---|---|
| `unit-tests-gate` | `unit-tests.yml` | pytest (Redis service); `alert-kafka`: the alert worker's consumer loop, ingestion's producer (through a broker outage) and the tenant-status stream against the compose Kafka broker; `spark-streaming`: the streaming and aggregation jobs' images against the compose Kafka, TimescaleDB and MinIO; `hub-docker`: the notebook volume reclaim against the runner's Docker daemon |
| `lint-gate` | `lint.yml` | ruff, exact pins + deps-resolve, eslint, frontend tests (vitest) |
| `images-gate` | `images.yml` | Spark/JupyterHub version parity, image build + smoke + Trivy, pip-audit / npm audit |
| `helm-gate` | `helm.yml` | `helm lint` / `helm template`, fail-closed placeholder-secret guard |
| `compose-smoke-gate` | `compose-smoke.yml` | The stack starts and stays up (unfiltered) |
| `db-tenant-isolation-gate` | `db-tenant-isolation.yml` | Live stack (`infrastructure/timescaledb/tests`, ADR-0026 dec 11): reader-role isolation, backup restore and rehearsal, object mirrors and notebook archives against MinIO and Docker; SQL-proxy cross-tenant isolation |
| `cold-layer-integration-gate` | `cold-layer-integration.yml` | Live stack: the cold exporter (export, verify, drop), the tenant-delete job's cold purge as its own principal against MinIO, the cold-store broker's reads and isolation |
| `tracking-gateway-integration-gate` | `tracking-gateway-integration.yml` | Tracking gateway proofs; upload path's serving half (register and deploy in the real ml_service); a notebook-registered version promoted with the sample its run logged |
| `model-artifact-roundtrip-gate` | `model-artifact-roundtrip.yml` | Parity, model artifact round-trip |
| `config-guards-gate` | `config-guards.yml` | Silent-omission list guards (`tests/config_guards`, ADR-0000 dec 3; among them: every `integration`-marked proof runs in a gated, path-filtered step that requires its stack, and is left out of the no-stack matrix); object-store principals against a real MinIO; platform alert rules under the pinned `promtool` and `amtool` (unfiltered) |

## Changing the list

The list is enforced only in the repository's branch-protection settings. A new workflow ends in a
gate, and that gate is added to this table and to branch protection in the same change:

```bash
gh api repos/<owner>/<repo>/branches/main/protection --jq '.required_status_checks.contexts'

gh api -X PATCH repos/<owner>/<repo>/branches/main/protection/required_status_checks \
  -f strict=false $(printf -- '-f contexts[]=%s ' unit-tests-gate lint-gate images-gate helm-gate \
  compose-smoke-gate db-tenant-isolation-gate cold-layer-integration-gate \
  tracking-gateway-integration-gate model-artifact-roundtrip-gate config-guards-gate)
```

## Box validation and releases

A commit on `main` is box-validated when the long-lived environment runs it, deployed by upgrade from
the last box-validated commit, and the end-to-end suite passed there
([ADR-0026](../../ADR/ADR-0026-release-model.md) dec 8–10). A versioned
release is cut only from such a commit.

| Task | Command |
|---|---|
| Deploy a commit by upgrade on the environment's host (ADR-0026 dec 8) | `scripts/box-deploy.sh [ref]` (default `origin/main`; disk floor, checkout, then the checked-out version of the script runs the rest: build, backup with the new image, schema, recreate, every running service that mounts a file the deploy changed recreated (`scripts/compose_file_mounts.py`), monitoring stack recreated; writes `.box-deployed`). The `ops-agent` profile is built and started when `.env` sets `OPS_AGENT_WEBHOOK_SECRET`. On failure the Spark services it stopped are started again and the deploy exits non-zero. Below `BOX_MIN_FREE_GB` (default `20`) free it prunes all build cache and dangling images, then refuses if still below |
| Run the suite on the environment's host (ADR-0026 dec 8) | `scripts/box-e2e.sh` (from the deployment's checkout; refuses local changes or a stack not deployed from `HEAD`) |
| Run it over SSH and post the `box-e2e` commit status | `scripts/box-validate.sh <ssh-target> [compose-dir]` (needs `gh`; refuses an upgrade from a commit without `box-e2e` success) |
| First validation of an environment | `BOX_FIRST_VALIDATION=1 scripts/box-validate.sh <ssh-target>` |
| Run one journey | `scripts/box-e2e.sh -k <name>` (arguments go to pytest) |
| Cut a release | `git tag v<version> <sha> && git push origin v<version>` |

| Item | Value |
|---|---|
| Suite | `tests/e2e/` (runner image `tests/e2e/Dockerfile`; ADR-0026 dec 10) |
| Journeys | Capability use and its record; a device reading through the certificate edge; a revoked device refused there once the edge re-reads its list, another device of the tenant still admitted; a model uploaded, registered on a bound configuration, deployed and scored by its rule; a tenant through deletion; the operator's user CLIs (seeded, listed, password reset with the old session and password refused, deleted); a platform alert explained by the infrastructure agent into a note in Loki, its receiver refusing a caller without the secret (where the agent is off: verified off) |
| Run's superuser | Created by `box-e2e.sh` in the oldest active tenant, removed before the result line; a run that cannot remove it is `failed` |
| Run's tenant | Suspended, deletion requested, then its devices revoked (`device-ca.sh revoke-tenant`; the job accepts only a CRL signed no earlier than the second of the request) at the end; the next run's deletion job removes it |
| Skips | Failures (`IF_E2E_REQUIRE=1`) |
| Release workflow | `release.yml`: refuses a tag whose commit is not on `main`, has no successful `box-e2e` status, or has no successful `Images` run on `main`; resolves every image of `images.yml` from `:sha-<short>` to its digest, then tags each digest with the version |
| Precondition (ADR-0026 dec 8) | The environment runs the commit under test (`scripts/box-deploy.sh`), with the notebook authoring image and the device CA (`scripts/device-ca.sh init`) |
| Validated bytes | The environment builds its own images; a release publishes the main pipeline's build of the same commit ([ADR-0026](../../ADR/ADR-0026-release-model.md) Consequences) |

| Symptom | Cause | Fix |
|---|---|---|
| `box-e2e: could not remove <email>` | The cleanup could not reach the database | `$RUN api-gateway python /scripts/manage_users.py delete --email <email> --yes` ([user management](user-management.md)) |
| Device journey times out | Spark streaming not running | `docker compose up -d spark-master spark-worker spark-streaming` |
| `box-deploy: … GB free after pruning … refusing` | Docker's disk is full beyond build cache | Free space (old images, volumes of removed services), or deploy with a lower `BOX_MIN_FREE_GB` knowingly |
| Release fails at `The commit is box-validated` | No `box-e2e` success on that commit (ADR-0026 dec 9) | `scripts/box-validate.sh` after deploying it |

## ADR-0026 dec 2

A pull request merges into `main` only when every gate in [Required checks on `main`](#required-checks-on-main) passed. Each gate fails when a job it needs failed or was cancelled. `compose-smoke-gate` is the required gate that starts the system. Box validation is a separate commit status, not a required check.

| Code | Role |
|---|---|
| `.github/workflows` | One `<name>-gate` job per workflow |
| `.github/workflows/compose-smoke.yml` | The required gate that starts the system |
| `tests/config_guards/test_alert_rules.py::test_every_ci_gate_checks_every_job_it_needs` | Proof: no gate passes a job it does not check |

## ADR-0026 dec 3, 4

The compose smoke gate starts the whole stack, upgrades it from the commit the change merges onto and judges liveness over an interval.

| Code | Role |
|---|---|
| `.github/workflows/compose-smoke.yml` | Whole-stack gate |

## ADR-0026 dec 5, 7

Every image is built and started; the newest tag tracks the main branch.

| Code | Role |
|---|---|
| `.github/workflows/images.yml` | Image build, start and tag |
| `.github/dependabot.yml` | Dependency updates target the main branch |

## ADR-0026 dec 6

A box finding CI can reproduce becomes a CI check, such as the exit codes of one-shot jobs.

| Code | Role |
|---|---|
| `tests/config_guards/test_one_shot_exit_codes.py` | One-shot failures are not exit 0 |

## ADR-0026 dec 8-10

The box run deploys a main commit, runs the end-to-end journeys and records the validated commit (see Box validation and releases).

| Code | Role |
|---|---|
| `scripts/box-deploy.sh` | Deploy a commit to the box |
| `scripts/box-e2e.sh` | Run the journeys |
| `scripts/box-validate.sh` | Deploy, run and record |
| `tests/e2e` | End-to-end journeys |

## ADR-0026 dec 9

A release is cut from a box-validated commit.

| Code | Role |
|---|---|
| `.github/workflows/release.yml` | Release |

## ADR-0026 dec 11

A proof runs against the real store, never a fake, and its stack is required in CI.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/tests/service_sql.py` | Real-database service SQL |
| `libs/industryflow_contracts/industryflow_contracts/testing.py` | Shared real-store fixtures |
| `services/alert_service/tests/test_kafka_consumer_integration.py` | Real Kafka consumer proof |
| `services/spark_jobs/tests/test_streaming_integration.py` | Real streaming proof |
| `services/ingestion_service/tests/test_tenant_status_integration.py` | Real tenant-status proof |
| `services/ingestion_service/tests/test_kafka_producer_integration.py` | Real producer proof |
| `services/tenant_delete/tests/test_cold_store_live.py` | Real object-store purge proof |
| `tests/config_guards/test_tests_use_real_redis.py` | No Redis fake |
| `tests/config_guards/test_tests_use_real_kafka.py` | No Kafka fake |
| `tests/config_guards/test_streaming_proof_runs.py` | The streaming proof runs |
| `tests/config_guards/test_integration_proofs_run.py` | Every integration proof runs in a gated step |
| `tests/config_guards/test_one_shot_exit_codes.py` | One-shot failures are not exit 0 |
