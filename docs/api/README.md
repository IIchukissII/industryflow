<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# API reference

Per-service API documentation. The interactive Swagger UI at `http://localhost:8000/docs`
(when the stack is running) is the most up-to-date reference.

| Document | Service |
|----------|---------|
| [API_Gateway_Documentation.md](API_Gateway_Documentation.md) | API Gateway (auth, sensors, routing) — port 8000 |
| [api_endpoints.md](api_endpoints.md) | Consolidated endpoint reference |
| [Alert_Service_API_Documentation.md](Alert_Service_API_Documentation.md) | Alert service (rules, alerts) — port 8001 |
| [ML_Service_API_Documentation.md](ML_Service_API_Documentation.md) | ML service (models, MLflow, inference) — port 8002 |
| [Feature_Engineering_API_Documentation.md](Feature_Engineering_API_Documentation.md) | Feature configuration & engineering |
| [Ingestion_Service_Technical_Documentation.md](Ingestion_Service_Technical_Documentation.md) | Sensor data ingestion |

> ⚠️ **Being modernized.** These documents predate the recent auth and tenancy changes and
> contain known inconsistencies — e.g. token lifetime (now a 30-min access token + refresh,
> not a 7-day JWT), the JWT now carrying a `company_id` claim (no more per-request schema
> scan), the secured `/api/inference/predict` and `/api/users` endpoints, and the new
> `/auth/login` · `/auth/refresh` · `/auth/logout` endpoints. See
> [authentication](../operations/authentication.md), **[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**,
> **[ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md)**, and the
> live Swagger UI for the current contract.
