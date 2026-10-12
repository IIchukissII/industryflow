<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# notebook_tracking_gateway

The trusted experiment-tracking gateway for authoring notebooks (ADR-0019). A kernel's MLflow
client points `MLFLOW_TRACKING_URI` at this gateway and sends its **tracking capability** as the
bearer token (`MLFLOW_TRACKING_TOKEN`). The gateway authenticates the capability, resolves it to
one tenant, and **forces every MLflow operation into that tenant's namespace** — so a notebook can
log runs and register models within its own tenant and nothing else. MLflow's broad backend and
object-store credentials live only here, never in the kernel (ADR-0013 dec 1, ADR-0019).

## Contents

- **`policy.py`** (pure, tested) — resolve a handle of exactly one audience (tracking or upload) to
  its tenant; the `tenant_<uuid>.` name prefix for experiments + registered models (qualify in /
  strip out / ownership predicate); the artifact object-store prefix and plain-key rule; the map of
  which MLflow REST fields are tenant-namespaced names.
- **`routes.py`** (pure, tested) — **default deny**: the MLflow routes the gateway forwards and the
  tenant rule each is held to. A route not listed, or not spelled canonically, is refused (403).
- **`scoping.py`** (pure, tested) — apply the name map: qualify request names, strip response names,
  and **drop any entry that is not the caller's tenant's** so a list call can never leak another
  tenant's experiment or model.
- **`gateway.py`** — the FastAPI app: bearer auth → route match → ownership lookups → request
  scoping → proxy to MLflow → response scoping. A request's fields are read from the one place
  MLflow reads them (query string for GET, JSON body otherwise); fields in the other place are
  refused (400), so what is checked is what MLflow acts on. Artifacts are answered with
  **tenant-scoped, per-object, short-TTL pre-signed URLs** (ADR-0019 dec 6) so bytes move directly
  kernel↔object-store. Written against injectable `Upstream`/`ArtifactStore`, so the orchestration
  is testable with a fake MLflow.
- **`admission.py`, `staging.py`** — the upload plane (ADR-0030): an externally-authored artifact is
  staged, judged on structure, then admitted into the tenant's prefix or discarded.
- **`tests/`** — unit tests run in the `unit-tests` CI workflow; `test_*_integration.py` run against
  a live MLflow + MinIO + Redis in the `tracking-gateway-integration` workflow.

## Status & boundary

**Verified in CI:** everything above against a fake upstream + store (unit), and the live path
against a real MLflow server and object store (integration): tenant namespacing, cross-tenant
refusal, artifact round trips through pre-signed URLs, the upload plane, and a gateway-logged model
loading on the serving side.

**Running the integration tests outside CI** where `PUBLIC_BASE_URL` is set: upload-staging URLs
are signed for the public edge, so the test container must trust the edge's CA
(`REQUESTS_CA_BUNDLE`).

## Develop

```bash
pip install -r requirements-dev.txt
pytest tests/
```
