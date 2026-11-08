<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Tracking gateway

The manual of [ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway-and-the-tracking-capability.md), [ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multi-tenancy.md), [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md), [ADR-0030](../../ADR/ADR-0030-admission-of-externally-authored-models.md): the experiment-tracking gateway for authoring notebooks, which resolves the kernel's tracking capability handle (`MLFLOW_TRACKING_TOKEN`) to one tenant and confines every MLflow call to that tenant's namespace.

## ADR-0019 dec 1, 5, 6

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/policy.py` | Resolve a handle of exactly one audience (tracking or upload); tenant name prefix; artifact prefix and plain-key rule; map of tenant-namespaced MLflow fields |
| `services/notebook_tracking_gateway/routes.py` | Default-deny route table: forwarded MLflow routes and the tenant rule each is held to |
| `services/notebook_tracking_gateway/scoping.py` | Qualify request names, strip response names, drop other tenants' entries |
| `services/notebook_tracking_gateway/gateway.py` | FastAPI app: auth → route match → ownership lookup → request scoping → MLflow → response scoping; injectable `Upstream` / `ArtifactStore` |

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | none | Liveness |
| GET, POST, PUT, PATCH, DELETE | `/api/2.0/mlflow/<endpoint>` | tracking handle | Allow-listed MLflow REST routes, tenant-scoped; any other route or non-canonical spelling: 403 |
| GET, PUT, DELETE | `/api/2.0/mlflow-artifacts/artifacts/<path>` | tracking handle | Key forced under the tenant prefix. GET/PUT: 307 to a per-object, short-TTL pre-signed URL; DELETE: done by the gateway. PUT/DELETE under `<tenant>/uploads/`: 403 |
| GET | `/api/2.0/mlflow-artifacts/artifacts` | tracking handle | Artifact listing, tenant-scoped |
| POST | `/api/2.0/industryflow-upload/stage` | upload handle | Stage an externally-authored artifact under `_upload-staging/` ([ADR-0030](../../ADR/ADR-0030-admission-of-externally-authored-models.md)) |
| POST | `/api/2.0/industryflow-upload/commit` | upload handle | Judge and admit into `<tenant>/uploads/`, or discard |
| DELETE | `/api/2.0/industryflow-operator/tenants/<company_id>` | `TRACKING_OPERATOR_TOKEN` | Empty a deleted tenant ([ADR-0033 dec 4](#adr-0033-dec-4)) |

| Rule | Behaviour |
|---|---|
| Field source | Fields are read from where MLflow reads them (query string for GET, JSON body otherwise); fields in the other place: 400 |
| Names | Experiments and registered models carry a `tenant_<uuid>.` prefix: qualified on request, stripped on response; entries of other tenants are dropped from lists |
| Commit | Spends the upload handle (one upload per handle); a manifest over 256 KiB is refused; members are promoted with an ETag-conditional copy, so bytes re-PUT after the verdict are refused (409) |

### Settings

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

### Tests

| Tests | Runs in |
|---|---|
| `tests/test_*.py` except `test_tracking_gateway_integration.py`, against a fake MLflow and store | `unit-tests.yml` |
| `tests/test_*_integration.py` (`-m integration`), against live MLflow + MinIO + Redis | `tracking-gateway-integration.yml` |

Unit-test command: [getting started](../../docs/getting-started.md#tests).
With `TRACKING_S3_PRESIGN_ENDPOINT_URL` (`PUBLIC_BASE_URL` in Compose) set, upload-staging URLs are
signed for the public edge: an integration run outside CI needs the edge's CA in `REQUESTS_CA_BUNDLE`.

## ADR-0013 dec 1, 2

The artifact store's read/write principal (`TRACKING_S3_ACCESS_KEY`, `TRACKING_S3_SECRET_KEY`) lives only
in the gateway; MLflow's database credential stays with the tracking server.

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/gateway.py` | Holds the object-store principal; presigns for the kernel |

## ADR-0030 dec 2, 10

The upload plane stages an externally-authored artifact under `_upload-staging/` and judges it on structure at commit; the endpoints are in [ADR-0019 dec 1, 5, 6](#endpoints).

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/staging.py` | Staging keys under `STAGING_ROOT = "_upload-staging/"` |
| `services/notebook_tracking_gateway/admission.py` | Structural judgement of a staged upload |

## ADR-0033 dec 4

`DELETE /api/2.0/industryflow-operator/tenants/<company_id>` deletes the tenant's registered models,
artifacts and staged uploads, renames its experiments to `_deleted.tenant_<…>.<id>` and soft-deletes
them with their runs and logged models. It answers what remains; experiments count until
`infrastructure/mlflow/collect_deleted.py` collects them. The route is absent when
`TRACKING_OPERATOR_TOKEN` is unset.

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/offboarding.py` | Empty a deleted tenant out of the tracking store; what remains |
