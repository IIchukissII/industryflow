<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Tracking gateway

The experiment-tracking gateway for authoring notebooks ([ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway.md)): it resolves the kernel's tracking capability handle (`MLFLOW_TRACKING_TOKEN`) to one tenant and confines every MLflow call to that tenant's namespace. The artifact store's read/write principal lives only here ([ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) dec 1); MLflow's database credential stays with the tracking server.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | Liveness |
| GET, POST, PUT, PATCH, DELETE | `/api/2.0/mlflow/<endpoint>` | tracking handle | Allow-listed MLflow REST routes, tenant-scoped; any other route or non-canonical spelling: 403 |
| GET, PUT, DELETE | `/api/2.0/mlflow-artifacts/artifacts/<path>` | tracking handle | Key forced under the tenant prefix. GET/PUT: 307 to a per-object, short-TTL pre-signed URL (ADR-0019 dec 6); DELETE: done by the gateway. PUT/DELETE under `<tenant>/uploads/`: 403 |
| GET | `/api/2.0/mlflow-artifacts/artifacts` | tracking handle | Artifact listing, tenant-scoped |
| POST | `/api/2.0/industryflow-upload/stage` | upload handle | Stage an externally-authored artifact under `_upload-staging/` ([ADR-0030](../../ADR/ADR-0030-externally-authored-model-admission.md)) |
| POST | `/api/2.0/industryflow-upload/commit` | upload handle | Judge and admit into `<tenant>/uploads/`, or discard |
| DELETE | `/api/2.0/industryflow-operator/tenants/<company_id>` | `TRACKING_OPERATOR_TOKEN` | Empty a deleted tenant ([ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) dec 4): delete its registered models, artifacts and staged uploads; rename its experiments to `_deleted.tenant_<…>.<id>` and soft-delete them with their runs and logged models. Answers what remains; experiments count until `infrastructure/mlflow/collect_deleted.py` collects them. Absent when the token is unset |

| Rule | Behaviour |
|---|---|
| Field source | Fields are read from where MLflow reads them (query string for GET, JSON body otherwise); fields in the other place: 400 |
| Names | Experiments and registered models carry a `tenant_<uuid>.` prefix: qualified on request, stripped on response; entries of other tenants are dropped from lists |
| Commit | Spends the upload handle (one upload per handle); a manifest over 256 KiB is refused; members are promoted with an ETag-conditional copy, so bytes re-PUT after the verdict are refused (409) |

## Files

| File | Content |
|---|---|
| `policy.py` | Resolve a handle of exactly one audience (tracking or upload); tenant name prefix; artifact prefix and plain-key rule; map of tenant-namespaced MLflow fields |
| `routes.py` | Default-deny route table: forwarded MLflow routes and the tenant rule each is held to |
| `scoping.py` | Qualify request names, strip response names, drop other tenants' entries |
| `gateway.py` | FastAPI app: auth → route match → ownership lookup → request scoping → MLflow → response scoping; injectable `Upstream` / `ArtifactStore` |
| `staging.py` | Staging keys under `STAGING_ROOT = "_upload-staging/"` |
| `admission.py` | Structural judgement of a staged upload |
| `offboarding.py` | Empty a deleted tenant out of the tracking store; what remains |

## Settings

| Name | Default | Meaning |
|---|---|---|
| `TRACKING_HOST` | `0.0.0.0` | Listen address |
| `TRACKING_PORT` | `5050` | Listen port |
| `TRACKING_MLFLOW_URL` | required | MLflow server |
| `TRACKING_REDIS_URL` | required | Capability store |
| `TRACKING_ARTIFACT_BUCKET` | required | MLflow artifact bucket |
| `TRACKING_PRESIGN_TTL_SECONDS` | `300` | Pre-signed URL lifetime |
| `TRACKING_S3_ENDPOINT_URL` | unset | Object store, interior endpoint |
| `TRACKING_S3_PRESIGN_ENDPOINT_URL` | unset (= interior endpoint) | Public edge origin that upload-staging URLs are signed for |
| `TRACKING_S3_ACCESS_KEY` | required | Object-store principal (`TRACKING_STORE_*`) |
| `TRACKING_S3_SECRET_KEY` | required | Its secret |
| `TRACKING_S3_REGION` | `us-east-1` | Signing region |
| `TRACKING_OPERATOR_TOKEN` | unset (no operator plane) | The tenant-delete job's secret |

## Tests

| Tests | Runs in |
|---|---|
| `tests/test_*.py` except `test_tracking_gateway_integration.py`, against a fake MLflow and store | `unit-tests.yml` |
| `tests/test_*_integration.py` (`-m integration`), against live MLflow + MinIO + Redis | `tracking-gateway-integration.yml` |

Unit-test command: [getting started](../../docs/getting-started.md#tests).
With `TRACKING_S3_PRESIGN_ENDPOINT_URL` (`PUBLIC_BASE_URL` in Compose) set, upload-staging URLs are
signed for the public edge: an integration run outside CI needs the edge's CA in `REQUESTS_CA_BUNDLE`.
