<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Architecture

Lean design overviews per subsystem; see the [ADRs](../../ADR/) for the decisions behind them.

| Document | Subsystem |
|----------|-----------|
| [data-and-storage.md](data-and-storage.md) | Schema-per-tenant database, time-series hypertables, compression, roles |
| [stream-processing.md](stream-processing.md) | Ingestion → Kafka → Spark → TimescaleDB, with at-least-once + idempotent writes |
| [ml-and-features.md](ml-and-features.md) | Config-driven feature engineering, MLflow models, inference, the plugin registry |
| [alerting.md](alerting.md) | Threshold & ML-based detection, dedup/cooldown, the detection worker |
| [notebooks.md](notebooks.md) | Embedded analytics & experimentation: per-tenant read-only role, tenant-scoped data path (ADR-0011/0012/0013) |

For the **plugin/extension** model that keeps these subsystems domain-generic, see
[operations/extensions.md](../operations/extensions.md) (ADR-0008/0010).
