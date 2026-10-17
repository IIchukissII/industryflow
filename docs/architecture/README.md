<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Architecture

Per-subsystem maps of what exists and where. The decisions behind each live in the [ADRs](../../ADR/);
terms are defined in the [glossary](../../ADR/GLOSSARY.md).

| Page | Subsystem | Deciding ADRs |
|---|---|---|
| [data-and-storage.md](data-and-storage.md) | Tenant schemas, tables, hypertables, roles, cold layer | [0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md), [0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |
| [cold-layer-evolution.md](cold-layer-evolution.md) | Cold-layer items deferred to a trigger, and the signal for each | [0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |
| [stream-processing.md](stream-processing.md) | Ingestion → Kafka → Spark → TimescaleDB, live-value read path, Spark upgrades | [0005](../../ADR/ADR-0005-kafka-delivery-semantics.md), [0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md), [0029](../../ADR/ADR-0029-spark-major-upgrade-checkpoint-cutover.md) |
| [ml-and-features.md](ml-and-features.md) | Feature engineering, model registry, inference, detectors, drift | [0010](../../ADR/ADR-0010-extension-plugin-mechanism.md), [0021](../../ADR/ADR-0021-model-drift-monitoring.md), [0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md), [0027](../../ADR/ADR-0027-model-artifact-supply-chain-parity.md), [0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) |
| [model-upload.md](model-upload.md) | Admitting an externally-authored model | [0030](../../ADR/ADR-0030-externally-authored-model-admission.md) |
| [alerting.md](alerting.md) | Alert API, detection worker, rule types, dedup, operator labels | [0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md), [0031](../../ADR/ADR-0031-alerting-http-surface-single-owner.md) |
| [notebooks.md](notebooks.md) | Notebook hub, capability planes, SQL proxy, tracking gateway, cold broker | [0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md)–[0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md), [0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md)–[0020](../../ADR/ADR-0020-notebook-per-user-persistence.md) |

Extensions and plugins: [operations/extensions.md](../operations/extensions.md)
([ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md),
[ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)).
