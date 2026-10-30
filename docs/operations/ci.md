<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# CI and required checks

The GitHub Actions workflows, their gate jobs, and the required checks on `main`
([ADR-0026](../../ADR/ADR-0026-release-model-and-the-compose-smoke-gate.md)).

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
| `unit-tests-gate` | `unit-tests.yml` | pytest |
| `lint-gate` | `lint.yml` | ruff, exact pins + deps-resolve, eslint, frontend tests (vitest) |
| `images-gate` | `images.yml` | Spark/JupyterHub version parity, image build + smoke + Trivy, pip-audit / npm audit |
| `helm-gate` | `helm.yml` | `helm lint` / `helm template`, fail-closed placeholder-secret guard |
| `compose-smoke-gate` | `compose-smoke.yml` | The stack starts and stays up (unfiltered) |
| `db-tenant-isolation-gate` | `db-tenant-isolation.yml` | Reader-role cross-tenant isolation |
| `cold-layer-integration-gate` | `cold-layer-integration.yml` | Cold layer integration |
| `tracking-gateway-integration-gate` | `tracking-gateway-integration.yml` | Tracking gateway proofs; upload path's serving half (register and deploy in the real ml_service) |
| `model-artifact-roundtrip-gate` | `model-artifact-roundtrip.yml` | Parity, model artifact round-trip |
| `config-guards-gate` | `config-guards.yml` | Silent-omission list guards (`tests/config_guards`); object-store principals against a real MinIO; platform alert rules under the pinned `promtool` and `amtool` (unfiltered) |

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
([ADR-0026](../../ADR/ADR-0026-release-model-and-the-compose-smoke-gate.md) dec 8–10). A versioned
release is cut only from such a commit.

| Task | Command |
|---|---|
| Run the suite on the environment's host | `scripts/box-e2e.sh` (from the deployment's checkout) |
| Run it over SSH and post the `box-e2e` commit status | `scripts/box-validate.sh <ssh-target> [compose-dir]` (needs `gh`) |
| Run one journey | `scripts/box-e2e.sh -k <name>` (arguments go to pytest) |
| Cut a release | `git tag v<version> <sha> && git push origin v<version>` |

| Item | Value |
|---|---|
| Suite | `tests/e2e/` (runner image `tests/e2e/Dockerfile`) |
| Journeys | Capability use and its record; a device reading through the certificate edge; a model uploaded, registered on a bound configuration, deployed and scored by its rule; a tenant through deletion |
| Run's superuser | Created by `box-e2e.sh` in the oldest active tenant, removed on exit |
| Run's tenant | Suspended with deletion requested at the end; the next run's deletion job removes it |
| Skips | Failures (`IF_E2E_REQUIRE=1`) |
| Release workflow | `release.yml`: refuses a tag whose commit is not on `main` or has no successful `box-e2e` status; publishes each image of `images.yml` from `:sha-<short>` under the tag, without rebuilding |
| Precondition | The environment runs the commit under test, with the notebook authoring image and the device CA (`scripts/device-ca.sh init`) |

| Symptom | Cause | Fix |
|---|---|---|
| `box-e2e: could not remove <email>` | The cleanup could not reach the database | `$RUN api-gateway python /scripts/manage_users.py delete --email <email> --yes` ([user management](user-management.md)) |
| Device journey times out | Spark streaming not running | `docker compose up -d spark-master spark-worker spark-streaming` |
| Release fails at `The commit is box-validated` | No `box-e2e` success on that commit | `scripts/box-validate.sh` after deploying it |
