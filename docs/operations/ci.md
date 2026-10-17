<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: AGPL-3.0-or-later
-->

# CI and required checks

The GitHub Actions workflows, their gate jobs, and the required checks on `main`
([ADR-0026](../../ADR/ADR-0026-release-model-and-the-compose-smoke-gate.md)).

## Gate convention

| Rule | Detail |
|------|--------|
| Triggers | Every workflow runs on `pull_request` and on `push` to `main` |
| One gate per workflow | Each workflow ends in a `<name>-gate` job with `if: always()`, so it always reports a status |
| Path-filtered workflows | A `changes` job (`dorny/paths-filter`) decides whether the workflow's paths changed. Unchanged: the work is skipped and the gate passes. Changed: the gate fails when any gated job failed or was cancelled, or when `changes` itself did not succeed |
| Unfiltered workflows | `compose-smoke.yml` and `config-guards.yml` have no `changes` job; their jobs run on every PR and the gate passes only when all of them succeeded |

## Required checks on `main`

| Gate | Workflow file | Gated jobs |
|------|---------------|------------|
| `unit-tests-gate` | `unit-tests.yml` | pytest |
| `lint-gate` | `lint.yml` | ruff, exact pins + deps-resolve, eslint, frontend tests (vitest) |
| `images-gate` | `images.yml` | Spark/JupyterHub version parity, image build + smoke + Trivy, pip-audit / npm audit |
| `helm-gate` | `helm.yml` | `helm lint` / `helm template`, fail-closed placeholder-secret guard |
| `compose-smoke-gate` | `compose-smoke.yml` | The stack starts and stays up (unfiltered) |
| `db-tenant-isolation-gate` | `db-tenant-isolation.yml` | Reader-role cross-tenant isolation |
| `cold-layer-integration-gate` | `cold-layer-integration.yml` | Cold layer integration |
| `tracking-gateway-integration-gate` | `tracking-gateway-integration.yml` | Tracking gateway proofs; upload path's serving half (register and deploy in the real ml_service) |
| `model-artifact-roundtrip-gate` | `model-artifact-roundtrip.yml` | Parity, model artifact round-trip |
| `config-guards-gate` | `config-guards.yml` | Silent-omission list guards (`tests/config_guards`); object-store principals against a real MinIO (unfiltered) |

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
