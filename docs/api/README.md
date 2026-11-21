<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# API reference

The HTTP services, their endpoint index, the edge routes and the conventions. Each FastAPI service
serves the full reference at `/docs` and `/openapi.json`; ports: components (ADR-0009 dec 6).

## API gateway endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| | `/auth/*`, `/users/me` | see page | Authentication (ADR-0004 dec 2) |
| | `/api/users`, `/api/companies` | `admin`, own tenant; company create, activation change, delete and deletion: superuser | User management (ADR-0004 dec 5) |
| GET | `/api/capability-use` | `admin` (own tenant), superuser (any) | Capability-use record (ADR-0042 dec 1-3, 5) |
| POST | `/api/notebook-ticket` | session | A single-use ticket into the notebook origin (ADR-0048 dec 3, 4) |
| GET | `/api/platform/switches` | superuser | Kill-switch runbook (ADR-0024 dec 2, 7) |
| PUT | `/api/platform/switches/{key}` | superuser; body `{"value": true\|false}` | Kill-switch runbook (ADR-0024 dec 2, 7) |
| GET | `/api/platform/switches/{key}/changes` | superuser | Kill-switch runbook (ADR-0024 dec 2, 7) |
| | `/api/alert-rules`, `/api/alerts` | session; writes `engineer`, `admin` | Alerting (ADR-0031 dec 1) |
| GET | `/api/measurements`, `/api/measurements/latest`, `/api/measurements/{sensor_id}` | session or `api` capability handle (read-only) | Raw readings |
| GET | `/api/aggregations/{window}`, `/api/aggregations/{window}/latest`, `/api/aggregations/combined/{sensor_id}` | session or `api` capability handle | Aggregates (`1min`, `5min`, `1hour`); `start`, `end`, `order`, `limit` ≤ 1000 |
| GET | `/api/training-data/equipment/{equipment_id}`, `…/stream` | session or `api` capability handle | Training frames |
| GET | `/api/equipment`, `/api/equipment/{id}`, `/api/equipment/{id}/sensors` | session | Equipment and sensors |
| POST, PUT, DELETE | `/api/equipment[/{id}]`, `/api/equipment/{id}/sensors[/{sensor_id}]`, `…/sensors/bulk` | `engineer`, `admin` | Manage equipment and sensors |
| PATCH | `/api/equipment/{id}/sensors/{sensor_id}` | `engineer`, `admin` | Set or clear `display_name` (trimmed, ≤ 100, empty → null); `sensor_name` is not editable |
| GET | `/api/cache/sensors` | session | Latest-value cache |
| WS | `/ws/sensors`, `/ws/sensors/{equipment_id}` | `if_access` cookie | Live values (ADR-0004 dec 9) |
| GET | `/health` | none | Liveness |

The gateway is the only HTTP surface for alert rules and alert history
(ADR-0031).

## ML service and ingestion endpoints

| Service | Endpoints |
|---|---|
| `ml-service-api` | ML and features (ADR-0021 dec 6) |
| Ingestion | `POST /ingest` on the mTLS edge; device certificate only (ADR-0002) |
| Tracking gateway | Tracking gateway (ADR-0019 dec 1, 5, 6) |

## Edge routes

The browser reaches every API same-origin through the HTTPS edge
([`services/frontend/nginx.conf.template`](../../services/frontend/nginx.conf.template)).

| Path prefix | Upstream |
|---|---|
| `/api/models`, `/api/registered-models`, `/api/experiments`, `/api/feature-configs`, `/api/ml/`, `/api/drift` | `ml-service-api` |
| `/api/2.0/industryflow-upload/` | Tracking gateway (ADR-0030) |
| `/mlflow/_upload-staging/` | Object store, pre-signed requests only |
| `/api/`, `/auth/`, `/users/`, `/health`, `/docs`, `/openapi.json`, `/ws/` | `api-gateway` |
| `/grafana/` | Grafana, only when `EDGE_GRAFANA` is set; signed on from a superuser session (ADR-0016 dec 6-7) |
| `/` | The single-page app |

The notebook hub is on an origin of its own, not a path of this one (ADR-0048 dec 1, 6).

## Conventions

| Topic | Rule | Source |
|---|---|---|
| Transport | HTTPS for external traffic; websocket over `wss://` | ADR-0004 |
| Request values | A path or query identifier is a UUID and a `limit` is bounded (alerts 1-1000); a value outside is 422, never a 500 from the database | `services/api_gateway/request_ids.py` |
| Browser auth | httpOnly cookies + double-submit CSRF (`X-CSRF-Token`) | authentication (ADR-0004 dec 3) |
| API client auth | `Authorization: Bearer <access token>` | authentication (ADR-0004 dec 2) |
| Notebook auth | Capability handle in `X-IF-Capability` | ADR-0015 |
| Service to service | `X-Internal-Service-Token` | authentication (ADR-0004 dec 4) |
| Tenancy | Tenant resolved from the verified identity (JWT claim, capability handle, device-certificate SAN); a `company_id` in a request body is ignored, except on internal-token calls to `/api/inference/predict` and `/api/drift/evaluate`, and on a superuser's `POST /api/users` | ADR-0003, ADR-0004 dec 5 |
