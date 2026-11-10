<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Licensing and framing

The manual of [ADR-0001](../../ADR/ADR-0001-industryflow-framing.md): the platform's scope, the licence each file carries, where the licence texts live, and where domain code goes.

## ADR-0001 dec 1

| Scope | Realised by |
|---|---|
| Multi-tenant ingestion | Ingestion service; tenant taken from the device certificate |
| Time-series storage | TimescaleDB, one schema per tenant |
| Stream processing | Spark jobs from Kafka into TimescaleDB |
| Threshold and ML-based alerting | Alert worker: `threshold`, `ml` and `statistical` rules |
| Model training and inference | Notebook hub for training; ml-service-api for scoring |
| Observability | Prometheus rules, Grafana, Loki |

| Code | Role |
|---|---|
| `services/ingestion_service/main.py` | Device ingestion |
| `infrastructure/timescaledb/init-scripts/04-complete-tenant-creation.sql::create_tenant_schema` | Per-tenant time-series schema |
| `services/spark_jobs/kafka_to_timescaledb.py` | Stream from Kafka into the tenant schemas |
| `services/alert_service/worker/rules_engine.py` | Threshold, ML and statistical rule evaluation |
| `services/notebook_hub/jupyterhub_config.py` | Notebook hub for model work |
| `services/ml_service/api/scoring.py` | Inference |
| `infrastructure/prometheus/rules/platform.yml` | Platform alert rules |

## ADR-0001 dec 2

| Content | Licence | Header |
|---|---|---|
| Code, configuration, scripts | AGPL-3.0-or-later | `SPDX-License-Identifier: AGPL-3.0-or-later` |
| Documentation, ADRs | CC-BY-SA-4.0 | `SPDX-License-Identifier: CC-BY-SA-4.0` |
| A file that cannot carry a header | As declared in `REUSE.toml` | — |

Code files carry an AGPL-3.0-or-later SPDX header, documentation a CC-BY-SA-4.0 one; files that cannot carry a header are declared in the manifest.

| Code | Role |
|---|---|
| `LICENSE` | AGPL-3.0-or-later text at the repository root |
| `LICENSES/AGPL-3.0-or-later.txt` | Code licence text |
| `LICENSES/CC-BY-SA-4.0.txt` | Documentation licence text |
| `REUSE.toml` | Licence of the files that carry no header |

## ADR-0001 dec 3

Every file in the repository carries one of the two licences of dec 2; `LICENSES` holds no other licence text, and no file is held back or gated.

| Code | Role |
|---|---|
| `LICENSE` | The licence of the whole repository's code |
| `REUSE.toml` | Covers the files without a header under the same two licences |

## ADR-0001 dec 4

A managed deployment is the same chart with external datastores (`infra.<dep>.inCluster: false`, [Helm chart](../../deploy/helm/industryflow/README.md#adr-0009-dec-3)); no template, service or image is specific to it. Compose and the chart pin each third-party image identically.

| Code | Role |
|---|---|
| `deploy/helm/industryflow/values.yaml` | `infra.<dep>.inCluster` and `external.*`: in-cluster or managed datastores |
| `tests/config_guards/test_image_pins.py::test_compose_and_chart_pin_the_same_image_identically` | Compose and the chart run the same third-party images |

## ADR-0001 dec 5

Not yet realised.

## ADR-0001 dec 6

Not yet realised.

## ADR-0001 dec 7

The core loads domain transforms and detectors from the modules named in `EXTENSION_MODULES`; it names no extension itself. `extensions/reference/reference_extension.py` is the in-repository example ([extensions](../operations/extensions.md)).

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py` | Extension contract: `register_transform`, `register_detector` |
| `services/ml_service/api/main.py` | Imports each module named in `EXTENSION_MODULES` |
| `extensions/reference/reference_extension.py` | Reference extension, registered without editing the core |
