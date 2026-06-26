<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Architecture documentation

Deep-dive design documents per subsystem. For the **decisions and rationale**, see the
[ADRs](../../ADR/); these documents describe the *what* and *how*.

| Document | Subsystem |
|----------|-----------|
| [IndustryFlow_Database_Architecture.md](IndustryFlow_Database_Architecture.md) | Schema-per-tenant database, tables, roles |
| [Spark_Streaming_Schema_Per_Tenant_Architecture.md](Spark_Streaming_Schema_Per_Tenant_Architecture.md) | Stream processing & tenant routing |
| [TimescaleDB_Compression_Technical_Specification.md](TimescaleDB_Compression_Technical_Specification.md) | Columnar compression & retention |
| [Alert_Detection_Service_Architecture.md](Alert_Detection_Service_Architecture.md) | Anomaly detection & alerting |
| [ML_Service_Architecture.md](ML_Service_Architecture.md) | Model training & management |
| [ML_Inference_and_Feature_Engineering.md](ML_Inference_and_Feature_Engineering.md) | Real-time inference pipeline |
| [Feature_Engineering_Service_Architecture.md](Feature_Engineering_Service_Architecture.md) | Feature store & computation |

> ⚠️ **Being modernized.** These predate the code-baseline work and have known
> inconsistencies with current code — notably: Spark now uses a **watermark + append +
> idempotent upserts** (not `update`-mode JDBC append); the Kafka consumer is
> **at-least-once** with manual commits and a dead-letter (not auto-commit); tenant routing
> reads a **validated `company_id` JWT claim** (not an N+1 schema scan), and `SET LOCAL
> search_path` is transaction-scoped; the measurement and aggregation tables now carry
> `UNIQUE` constraints for idempotency. The relevant decisions are
> **[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**,
> **[ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md)**, and
> **[ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md)**. The SQL init
> scripts under `infrastructure/timescaledb/init-scripts/` are authoritative for the schema.
