<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow documentation

| Area | Start here |
|------|-----------|
| **Getting started & operations** | [getting-started.md](getting-started.md) — setup, configuration, API examples, troubleshooting |
| **Operations guides** | [operations/](operations/) — [authentication](operations/authentication.md) · [user management](operations/user-management.md) · [TLS & internal CA](operations/tls.md) · [device mTLS](operations/device-mtls.md) · [monitoring](operations/monitoring.md) · [backup & recovery](operations/backup-and-recovery.md) |
| **Architecture** | [architecture/](architecture/README.md) — data & storage, stream processing, ML & features, alerting |
| **API reference** | [api/](api/README.md) — services map; the live Swagger UI is authoritative |
| **Deployment** | [../deploy/helm/industryflow/](../deploy/helm/industryflow/) — the Helm chart for Kubernetes (ADR-0009); [../deploy/observability/](../deploy/observability/README.md) — cluster monitoring stack (ADR-0016) |
| **Extensions** | [operations/extensions.md](operations/extensions.md) — the plugin contracts for feature transforms & detectors (ADR-0008/0010) |
| **Decisions (the *why*)** | [../ADR/](../ADR/) — Architecture Decision Records (ADR-0000 … ADR-0016) |

## Source of truth

Per **[ADR-0000](../ADR/ADR-0000-decision-records-and-source-of-truth.md)**, the **ADRs own
the rationale** behind the platform's design, and the **code and config are authoritative
for behavior** (`docker-compose.yml`, `.env.example`, the SQL init scripts, the service
modules). The `architecture/` docs are lean overviews and the `api/` reference defers to the
live Swagger UI — where a guide and the code disagree, the code wins and the guide is a bug.
