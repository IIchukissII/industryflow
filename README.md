<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow

**Open-core, general-purpose industrial-IoT data platform: device ingestion, stream processing, per-tenant isolation, anomaly detection, alerting and embedded notebooks.**

[![Unit tests](https://github.com/IIchukissII/industryflow/actions/workflows/unit-tests.yml/badge.svg)](https://github.com/IIchukissII/industryflow/actions/workflows/unit-tests.yml)
[![DB tenant isolation](https://github.com/IIchukissII/industryflow/actions/workflows/db-tenant-isolation.yml/badge.svg)](https://github.com/IIchukissII/industryflow/actions/workflows/db-tenant-isolation.yml)
[![Helm](https://github.com/IIchukissII/industryflow/actions/workflows/helm.yml/badge.svg)](https://github.com/IIchukissII/industryflow/actions/workflows/helm.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="img/industryflow-logo-mono-dark.svg" />
  <img src="img/industryflow-logo-mono-light.svg" alt="IndustryFlow" width="280" />
</picture>

## Capabilities

| Capability | Implementation | Decision |
|---|---|---|
| Device ingestion | mTLS edge; tenant taken from the device certificate | [ADR-0002](ADR/ADR-0002-ingestion-authentication-and-device-identity.md), [ADR-0007](ADR/ADR-0007-public-key-infrastructure.md) |
| Stream processing | Kafka → Spark → TimescaleDB, at-least-once with idempotent writes | [ADR-0005](ADR/ADR-0005-kafka-delivery-semantics.md), [ADR-0006](ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) |
| Multi-tenancy | Schema-per-tenant; tenants provisioned at runtime | [ADR-0003](ADR/ADR-0003-tenant-to-schema-resolution.md), [ADR-0033](ADR/ADR-0033-tenant-lifecycle.md) |
| Schema migrations | One ordered, idempotent script sequence applied by a runner and recorded in a ledger, fresh and live alike | [ADR-0034](ADR/ADR-0034-platform-schema-migration-strategy.md) |
| Anomaly detection | Feature transforms and detectors over MLflow-registered models | [ADR-0023](ADR/ADR-0023-stream-materialized-feature-engineering.md), [ADR-0028](ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) |
| Alerting | Threshold, ML and statistical (model-drift) rules; operator labels | [ADR-0021](ADR/ADR-0021-model-drift-monitoring.md), [ADR-0022](ADR/ADR-0022-concept-drift-operator-feedback.md), [ADR-0031](ADR/ADR-0031-alerting-http-surface-single-owner.md) |
| Authentication | HTTPS edge, httpOnly-cookie + CSRF platform sessions, bearer tokens for API clients | [ADR-0004](ADR/ADR-0004-api-authentication-sessions-and-transport.md) |
| Embedded notebooks | Per-user notebook hub with capability handles, SQL proxy, tracking gateway; experiments browsed in the console | [ADR-0011](ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md), [ADR-0015](ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) |
| Cold layer | Parquet history in object storage | [ADR-0025](ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |
| Extensions | Domain feature transforms and detectors as plugins | [ADR-0008](ADR/ADR-0008-extension-and-plugin-interface.md), [ADR-0010](ADR/ADR-0010-extension-plugin-mechanism.md) |
| Deployment | Docker Compose (single host), Helm chart (Kubernetes) | [ADR-0009](ADR/ADR-0009-kubernetes-deployment-and-packaging.md) |
| Observability | Prometheus, Grafana, Loki; platform alert rules shared by Compose and the chart, routed by Alertmanager | [ADR-0016](ADR/ADR-0016-observability-and-monitoring-integration.md) |

## Technology

| Layer | Components |
|---|---|
| Services | Python 3.14, FastAPI |
| Streaming | Apache Kafka, Apache Spark 4.1 |
| Storage | PostgreSQL 15 + TimescaleDB, Redis, MinIO |
| ML | MLflow |
| Frontend | React 19, Vite, nginx |
| Observability | Prometheus, Alertmanager, Grafana, Loki |

## Documentation

| Page | Covers |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | Running the Compose stack |
| [docs/README.md](docs/README.md) | Index of every page |
| [ADR/](ADR/), [ADR/GLOSSARY.md](ADR/GLOSSARY.md) | Decisions and vocabulary |

## Contributing

| Step | Rule |
|---|---|
| Branch | From `main` |
| Python style | `ruff` (`ruff.toml`), gated in CI ([CI](docs/operations/ci.md)) |
| Architecture | Decisions are ADRs ([ADR-0000](ADR/ADR-0000-decision-records-and-source-of-truth.md)) |
| Change | Tests and the affected docs in the same pull request |

## License

[AGPL-3.0-or-later](LICENSE) © 2026 The IndustryFlow contributors
