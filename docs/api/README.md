<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# API reference

Map of the HTTP services and the edge routes that reach them. Each FastAPI service serves the full
reference at `/docs` and `/openapi.json`.

## Services

| Service | Local port | Swagger | Routes |
|---------|-----------|---------|--------|
| API gateway | 8000 | `http://localhost:8000/docs` | `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/jwt/*`, `/auth/verify`, `/users/me`, `/api/users`, `/api/measurements`, `/api/aggregations`, `/api/training-data`, `/api/equipment`, `/api/companies`, `/api/alert-rules`, `/api/alerts` (incl. `PATCH /api/alerts/{id}/label`, `GET /api/alerts/label-metrics`), `/api/cache`, `/ws/sensors`, `/health` |
| ML service | 8002 | `http://localhost:8002/docs` | `/api/models` (incl. `POST /api/models/upload-capability`), `/api/registered-models`, `/api/experiments`, `/api/feature-configs`, `/api/inference/predict`, `/api/drift/evaluate`, `/api/ml/capabilities`, `/health` |
| Ingestion | — (mTLS edge, `INGESTION_MTLS_PORT`) | — | `POST /ingest`; device certificate only, no session or bearer token ([device mTLS](../operations/device-mtls.md)) |

The gateway is the only HTTP surface for alert rules and alert history
([ADR-0031](../../ADR/ADR-0031-alerting-http-surface-single-owner.md)).

## Edge routes

The browser reaches every API same-origin through the HTTPS edge
([`services/frontend/nginx.conf.template`](../../services/frontend/nginx.conf.template)).

| Path prefix | Upstream |
|-------------|----------|
| `/api/models`, `/api/registered-models`, `/api/experiments`, `/api/feature-configs`, `/api/ml/`, `/api/drift` | ML service |
| `/api/2.0/industryflow-upload/` | notebook tracking gateway (model upload, [model upload](../architecture/model-upload.md)) |
| `/mlflow/_upload-staging/` | object store, pre-signed upload staging |
| `/api/`, `/auth/`, `/users/`, `/health`, `/docs`, `/openapi.json`, `/ws/` | API gateway |
| `/jupyter/` | notebook SSO → hub |

## Conventions

| Topic | Rule | Source |
|-------|------|--------|
| Transport | HTTPS for external traffic; websocket over `wss://` | [ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md) |
| Browser auth | httpOnly cookies + double-submit CSRF (`X-CSRF-Token`) | [authentication](../operations/authentication.md) |
| API client auth | `Authorization: Bearer <token>` | [authentication](../operations/authentication.md) |
| Notebook auth | capability handle in `X-IF-Capability` | [ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) |
| Tenancy | Tenant schema resolved from the verified identity (JWT claim, capability, or device-certificate SAN); a `company_id` in a request body is ignored | [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
| Service-to-service | `X-Internal-Service-Token` carrying `INTERNAL_SERVICE_TOKEN`; the internal path is disabled when unset. Only such calls to `/api/inference/predict` may name a `company_id` | [authentication](../operations/authentication.md) |
