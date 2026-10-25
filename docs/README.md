<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow documentation

Index of every page under `docs/` and of the component READMEs. Decisions live in the
[ADRs](../ADR/); terms are defined in the [glossary](../ADR/GLOSSARY.md).

## Start

| Page | Covers |
|---|---|
| [getting started](getting-started.md) | Compose stack: configuration, first tenant and admin, components and ports, tests |
| [API reference](api/README.md) | HTTP services, endpoint index, edge routes, conventions |

## Architecture

| Page | Covers |
|---|---|
| [data and storage](architecture/data-and-storage.md) | Tenant identity and names, tables, cold layer |
| [stream processing](architecture/stream-processing.md) | Ingestion → Kafka → Spark → TimescaleDB, live-value read path, Spark upgrades |
| [ML and features](architecture/ml-and-features.md) | Feature configs, model registry, inference, detectors, drift |
| [model upload](architecture/model-upload.md) | Admission of an externally-authored model |
| [alerting](architecture/alerting.md) | Alert API, detection worker, rule types, dedup, operator labels |
| [notebooks](architecture/notebooks.md) | Notebook hub, profiles, capability handles, planes |
| [cold layer evolution](architecture/cold-layer-evolution.md) | Cold-layer items deferred to a trigger |

## Operations

| Page | Covers |
|---|---|
| [authentication](operations/authentication.md) | Platform sessions, tokens, revocation, auth endpoints |
| [user management](operations/user-management.md) | Roles, users, tenants |
| [TLS](operations/tls.md) | HTTPS edge, internal edge+DB CA, database TLS |
| [device mTLS](operations/device-mtls.md) | Device CA, device certificates, CRL |
| [extensions](operations/extensions.md) | Plugin contracts for feature transforms and detectors |
| [monitoring](operations/monitoring.md) | Compose metrics, logs, dashboards; kill-switch runbook |
| [backup and recovery](operations/backup-and-recovery.md) | Backups and restore |
| [high availability](operations/high-availability.md) | Disruption budgets, autoscaling, stateful backends |
| [capacity](operations/capacity.md) | Measured ingest capacity on one host, load-test tools |
| [production readiness](operations/production-readiness.md) | Readiness per area and open gaps |
| [CI](operations/ci.md) | CI workflows and required checks |

## Component READMEs

| Page | Covers |
|---|---|
| [deploy/helm/industryflow](../deploy/helm/industryflow/README.md) | Helm chart |
| [deploy/observability](../deploy/observability/README.md) | Cluster monitoring stack |
| [infrastructure/timescaledb/init-scripts](../infrastructure/timescaledb/init-scripts/README.md) | Database schema scripts, the migration runner and roles |
| [services/frontend](../services/frontend/README.md) | Frontend and TLS edge |
| [services/notebook_hub](../services/notebook_hub/README.md) | Notebook hub |
| [services/notebook_sql_proxy](../services/notebook_sql_proxy/README.md) | SQL proxy |
| [services/notebook_tracking_gateway](../services/notebook_tracking_gateway/README.md) | Tracking gateway |
| [services/ml_service/notebooks/utils](../services/ml_service/notebooks/utils/README.md) | Notebook feature-config and baseline helpers |
| [clients/python](../clients/python/README.md) | Python notebook client |
| [extensions/tep-reference](../extensions/tep-reference/README.md) | Reference extension and data producer |
