<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# API reference

The HTTP services, their endpoint index, the edge routes and the conventions. Each FastAPI service
serves the full reference at `/docs` and `/openapi.json`; ports: [components](../getting-started.md#components).

## API gateway endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| | `/auth/*`, `/users/me` | see page | [Authentication](../operations/authentication.md#endpoints) |
| | `/api/users`, `/api/companies` | `admin` | [User management](../operations/user-management.md#tenant-scoping) |
| | `/api/alert-rules`, `/api/alerts` | session; writes `engineer`, `admin` | [Alerting](../architecture/alerting.md#endpoints) |
| GET | `/api/measurements`, `/api/measurements/latest`, `/api/measurements/{sensor_id}` | session or `api` capability handle (read-only) | Raw readings |
| GET | `/api/aggregations/{window}`, `/api/aggregations/{window}/latest`, `/api/aggregations/combined/{sensor_id}` | session or `api` capability handle | Aggregates (`1min`, `5min`, `1hour`); `start`, `end`, `order`, `limit` ≤ 1000 |
| GET | `/api/training-data/equipment/{equipment_id}`, `…/stream` | session or `api` capability handle | Training frames |
| GET | `/api/equipment`, `/api/equipment/{id}`, `/api/equipment/{id}/sensors` | session | Equipment and sensors |
| POST, PUT, DELETE | `/api/equipment[/{id}]`, `/api/equipment/{id}/sensors[/{sensor_id}]`, `…/sensors/bulk` | `engineer`, `admin` | Manage equipment and sensors |
| GET | `/api/cache/sensors` | session | Latest-value cache |
| WS | `/ws/sensors`, `/ws/sensors/{equipment_id}` | `if_access` cookie | Live values ([websockets](../operations/authentication.md#websockets)) |
| GET | `/health` | none | Liveness |

The gateway is the only HTTP surface for alert rules and alert history
([ADR-0031](../../ADR/ADR-0031-alerting-http-surface-single-owner.md)).

## ML service and ingestion endpoints

| Service | Endpoints |
|---|---|
| `ml-service-api` | [ML and features](../architecture/ml-and-features.md#endpoints) |
| Ingestion | `POST /ingest` on the mTLS edge; device certificate only ([device mTLS](../operations/device-mtls.md)) |
| Tracking gateway | [Tracking gateway](../../services/notebook_tracking_gateway/README.md#endpoints) |

## Edge routes

The browser reaches every API same-origin through the HTTPS edge
([`services/frontend/nginx.conf.template`](../../services/frontend/nginx.conf.template)).

| Path prefix | Upstream |
|---|---|
| `/api/models`, `/api/registered-models`, `/api/experiments`, `/api/feature-configs`, `/api/ml/`, `/api/drift` | `ml-service-api` |
| `/api/2.0/industryflow-upload/` | Tracking gateway ([model upload](../architecture/model-upload.md)) |
| `/mlflow/_upload-staging/` | Object store, pre-signed requests only |
| `/api/`, `/auth/`, `/users/`, `/health`, `/docs`, `/openapi.json`, `/ws/` | `api-gateway` |
| `/jupyter/` | Notebook SSO → notebook hub ([ADR-0014](../../ADR/ADR-0014-notebook-hub-single-sign-on.md)) |
| `/` | The single-page app |

## Conventions

| Topic | Rule | Source |
|---|---|---|
| Transport | HTTPS for external traffic; websocket over `wss://` | [ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md) |
| Browser auth | httpOnly cookies + double-submit CSRF (`X-CSRF-Token`) | [authentication](../operations/authentication.md#browser-cookies) |
| API client auth | `Authorization: Bearer <access token>` | [authentication](../operations/authentication.md#tokens) |
| Notebook auth | Capability handle in `X-IF-Capability` | [ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) |
| Service to service | `X-Internal-Service-Token` | [authentication](../operations/authentication.md#configuration) |
| Tenancy | Tenant resolved from the verified identity (JWT claim, capability handle, device-certificate SAN); a `company_id` in a request body is ignored, except on internal-token calls to `/api/inference/predict` and `/api/drift/evaluate` | [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
