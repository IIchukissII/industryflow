<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# API reference

The **authoritative, always-current** endpoint reference is the interactive Swagger UI each
FastAPI service serves at `/docs` (and `/openapi.json`) when running — e.g.
`http://localhost:8000/docs` for the gateway. This page is the lean map; it does not restate
every schema.

Authentication for all human/service calls is described in
**[operations/authentication.md](../operations/authentication.md)**: browsers use httpOnly
cookies + CSRF over HTTPS; API/service clients use a bearer token. Every data endpoint is
authenticated and **tenant-scoped from the verified identity** — the `company_id` is never
taken from the request body.

## Services

| Service | Port | Swagger | What it exposes |
|---------|------|---------|-----------------|
| **API Gateway** | 8000 | `/docs` | Auth (`/auth/login`, `/auth/refresh`, `/auth/logout`, `/users/*`), sensor measurements & aggregations, equipment, companies, alert rules & history, cache, and the sensor websocket (`/ws/sensors`). The browser's single same-origin entry point. |
| **Alert Service** | 8001 | `/docs` | Alert-rule CRUD and alert history for the tenant. The detection worker is a separate Kafka consumer with no HTTP API. |
| **ML Service** | 8002 | `/docs` | Models, MLflow experiments/runs, feature configs, and real-time inference (`/api/inference/predict`). Inference engineers features and scores them through the [extension](../operations/extensions.md) registry. |
| **Ingestion** | via mTLS edge | — | Device sensor ingestion. Reached only through the mutual-TLS edge; the tenant comes from the verified device certificate. See **[device-mtls.md](../operations/device-mtls.md)**. Not a browser/JWT API. |

## Conventions

- **Transport:** HTTPS for all external traffic; the websocket is `wss://`. The browser app is
  served same-origin behind the TLS edge, so the gateway, ML, and alert APIs are reachable
  under one origin via `/api/*`, `/auth/*`, `/users/*` (see the frontend nginx config).
- **Tenancy:** the tenant schema is resolved from the verified `company_id` (JWT claim for the
  API, certificate SAN for ingestion) — **[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**.
- **Service-to-service:** internal calls (e.g. the alert worker → ML inference) authenticate
  with the shared `INTERNAL_SERVICE_TOKEN` header, failing closed if unset.
