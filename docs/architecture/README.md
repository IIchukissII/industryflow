<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Architecture

Lean design overviews per subsystem; see the [ADRs](../../ADR/) for the decisions behind them.

| Document | Subsystem |
|----------|-----------|
| [data-and-storage.md](data-and-storage.md) | Schema-per-tenant database, time-series hypertables, compression, roles |
| [cold-layer-evolution.md](cold-layer-evolution.md) | Cold layer (ADR-0025) deferred decisions, their triggers, and the signals that detect them |
| [stream-processing.md](stream-processing.md) | Ingestion → Kafka → Spark → TimescaleDB, with at-least-once + idempotent writes |
| [ml-and-features.md](ml-and-features.md) | Config-driven feature engineering, MLflow models, inference, the plugin registry; model trust by provenance (ADR-0027) |
| [model-upload.md](model-upload.md) | Admitting a model the platform never watched being made (ADR-0030): staged bytes, the structural refusal, and the two gates that judge it |
| [alerting.md](alerting.md) | Threshold & ML-based detection, durable dedup under redelivery (ADR-0005), the detection worker; the gateway is the only alerting API (ADR-0031) |
| [notebooks.md](notebooks.md) | Embedded analytics & experimentation: per-tenant read-only role, tenant-scoped data path, dedicated kernel network, default-deny tracking gateway (ADR-0011→0015, ADR-0018→0020) |

For the **plugin/extension** model that keeps these subsystems domain-generic, see
[operations/extensions.md](../operations/extensions.md) (ADR-0008/0010).
