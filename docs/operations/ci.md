<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: AGPL-3.0-or-later
-->

# CI and required checks

`main` means every required check passed, and one of them starts the system
([ADR-0026](../../ADR/ADR-0026-release-model-and-the-compose-smoke-gate.md)).

Every workflow runs on every pull request and ends in one **gate** job. A `changes` job decides
whether the workflow's paths changed. If they did not, the work is skipped and the gate passes.
If they did, the gate fails when any gated job failed or was cancelled. So a gate always reports a
status, which is what lets it be required without blocking unrelated PRs.

## Required checks on `main`

| Gate | Workflow |
|------|----------|
| `unit-tests-gate` | Unit tests |
| `lint-gate` | Lint (ruff, exact pins, deps-resolve, eslint, frontend tests) |
| `images-gate` | Images (build, smoke, Trivy) |
| `helm-gate` | Helm |
| `compose-smoke-gate` | Compose smoke (the stack starts and stays up) |
| `db-tenant-isolation-gate` | DB tenant isolation |
| `cold-layer-integration-gate` | Cold layer integration |
| `tracking-gateway-integration-gate` | Tracking gateway integration |
| `model-artifact-roundtrip-gate` | Model artifact round-trip |
| `config-guards-gate` | Config guards (silent-omission lists: path filters, image pins, chart/compose wiring; object-store principals against a real MinIO) |

**This list is enforced only in the repository's branch-protection settings, so it can drift
silently.** A new workflow must end in a gate, and that gate must be added here and to branch
protection in the same change:

```bash
gh api -X PATCH repos/<owner>/<repo>/branches/main/protection/required_status_checks \
  -f strict=false $(printf -- '-f contexts[]=%s ' unit-tests-gate lint-gate images-gate helm-gate \
  compose-smoke-gate db-tenant-isolation-gate cold-layer-integration-gate \
  tracking-gateway-integration-gate model-artifact-roundtrip-gate config-guards-gate)
```
