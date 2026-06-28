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

- **`policy.py`** (pure, tested) — resolve a tracking handle to its tenant; the `tenant_<uuid>/`
  name prefix for experiments + registered models (qualify in / strip out / ownership predicate);
  the artifact object-store prefix; the map of which MLflow REST fields are tenant-namespaced names.
- **`scoping.py`** (pure, tested) — apply that map: qualify request names, strip response names,
  and **drop any entry that is not the caller's tenant's** so a list call can never leak another
  tenant's experiment or model.
- **`gateway.py`** — the FastAPI app: bearer auth → request scoping → proxy to MLflow → response
  scoping, with id-bearing calls (`run_id`/`experiment_id`) refused unless the referenced
  experiment is the tenant's, and artifacts answered with **tenant-scoped, per-object, short-TTL
  pre-signed URLs** (ADR-0019 dec 6) so bytes move directly kernel↔object-store. Written against
  injectable `Upstream`/`ArtifactSigner` so the orchestration is testable with a fake MLflow.
- **`tests/`** — run in the `unit-tests` CI workflow.

## Status & boundary

**Verified (unit-tested, no cluster):** the tenant-name policy, the request/response scoping
(including foreign-entry dropping), the bearer auth, the id-ownership refusal, and the artifact
key-scoping + pre-sign call — all against a fake upstream + store.

**NOT cluster-validated:** the live adapters in `main()` — the httpx proxy to a real MLflow server
and boto3 pre-signing against the real object store. End-to-end validation (a notebook logging a
run + registering a model on its tenant's data, cross-tenant refused) needs a running MLflow +
object store and is the issue #19-style follow-up.

## Develop

```bash
pip install -r requirements-dev.txt
pytest tests/
```
