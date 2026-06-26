<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow documentation

| Area | Start here |
|------|-----------|
| **Getting started & operations** | [getting-started.md](getting-started.md) — setup, configuration, API examples, troubleshooting |
| **Operations guides** | [operations/](operations/) — [authentication](operations/authentication.md) · [user management](operations/user-management.md) · [TLS & internal CA](operations/tls.md) · [monitoring](operations/monitoring.md) |
| **Architecture** | [architecture/](architecture/README.md) — database, Spark streaming, ML, alerting, feature engineering |
| **API reference** | [api/](api/README.md) — per-service API documentation |
| **Decisions (the *why*)** | [../ADR/](../ADR/) — Architecture Decision Records (ADR-0000 … ADR-0009) |

## Source of truth

Per **[ADR-0000](../ADR/ADR-0000-decision-records-and-source-of-truth.md)**, the **ADRs own
the rationale** behind the platform's design, and the **code and config are authoritative
for behavior** (`docker-compose.yml`, `.env.example`, the SQL init scripts, the service
modules). Where a guide and the code disagree, the code wins — and the guide is a bug.

> **Note:** the detailed `architecture/` and `api/` documents predate the recent
> code-baseline work (auth refactor, idempotent Spark writes, at-least-once Kafka, tenant
> resolution) and are being modernized. Until then, treat their specifics as indicative and
> cross-check against the ADRs and the code. Each directory's `README` flags what is current.
