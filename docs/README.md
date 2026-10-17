<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow documentation

Index of every page under `docs/` and the component READMEs elsewhere in the repository.

## Start

| Page | Covers |
|------|--------|
| [getting-started.md](getting-started.md) | Local compose stack: setup, first tenant and admin, endpoints, API examples, troubleshooting |
| [api/README.md](api/README.md) | Service map, edge routes, API conventions |

## Architecture

| Page | Covers |
|------|--------|
| [architecture/README.md](architecture/README.md) | Index of the subsystem overviews |
| [architecture/data-and-storage.md](architecture/data-and-storage.md) | Schema-per-tenant database, hypertables, compression, roles |
| [architecture/stream-processing.md](architecture/stream-processing.md) | Ingestion → Kafka → Spark → TimescaleDB |
| [architecture/ml-and-features.md](architecture/ml-and-features.md) | Feature engineering, models, inference, plugin registry |
| [architecture/model-upload.md](architecture/model-upload.md) | Admission of an externally-authored model |
| [architecture/alerting.md](architecture/alerting.md) | Detection worker, alert rules, dedup |
| [architecture/notebooks.md](architecture/notebooks.md) | Embedded notebooks: hub, capabilities, SQL proxy, tracking gateway |
| [architecture/cold-layer-evolution.md](architecture/cold-layer-evolution.md) | Cold layer: deferred decisions, triggers, detection signals |

## Operations

| Page | Covers |
|------|--------|
| [operations/authentication.md](operations/authentication.md) | Browser sessions, tokens, auth endpoints |
| [operations/user-management.md](operations/user-management.md) | Users, roles, superusers |
| [operations/tls.md](operations/tls.md) | HTTPS edge, internal CA |
| [operations/device-mtls.md](operations/device-mtls.md) | Device CA, device certificates, CRL |
| [operations/extensions.md](operations/extensions.md) | Plugin contracts for feature transforms and detectors |
| [operations/monitoring.md](operations/monitoring.md) | Metrics, logs, dashboards |
| [operations/backup-and-recovery.md](operations/backup-and-recovery.md) | Backups and restore |
| [operations/high-availability.md](operations/high-availability.md) | Resilience and HA |
| [operations/production-readiness.md](operations/production-readiness.md) | Production-readiness review and backlog |
| [operations/ci.md](operations/ci.md) | CI workflows and required checks |

## Elsewhere in the repository

| Path | Covers |
|------|--------|
| [../ADR/](../ADR/) | Architecture Decision Records; [ADR-0000](../ADR/ADR-0000-decision-records-and-source-of-truth.md) defines what is authoritative |
| [../ADR/GLOSSARY.md](../ADR/GLOSSARY.md) | Controlled vocabulary |
| [../deploy/helm/industryflow/README.md](../deploy/helm/industryflow/README.md) | Helm chart for Kubernetes |
| [../deploy/observability/README.md](../deploy/observability/README.md) | Cluster monitoring stack |
| [../clients/python/README.md](../clients/python/README.md) | Python notebook client |
| [../extensions/tep-reference/README.md](../extensions/tep-reference/README.md) | Reference extension and data producer |
| [../infrastructure/timescaledb/init-scripts/README.md](../infrastructure/timescaledb/init-scripts/README.md) | Database init scripts |
| [../services/frontend/README.md](../services/frontend/README.md) | Frontend and TLS edge |
| [../services/notebook_hub/README.md](../services/notebook_hub/README.md) | Notebook hub |
| [../services/notebook_sql_proxy/README.md](../services/notebook_sql_proxy/README.md) | SQL access proxy |
| [../services/notebook_tracking_gateway/README.md](../services/notebook_tracking_gateway/README.md) | Experiment-tracking gateway |
