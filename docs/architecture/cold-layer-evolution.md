<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Cold-layer deferred items

The manual of [ADR-0025](../../ADR/ADR-0025-cold-layer-for-historical-telemetry-in-an-open-columnar-format.md): the cold-layer items deferred to a trigger, the signal that shows each trigger has fired, and the code each would touch.

None of the deferred items is built. The built layer: [cold layer](data-and-storage.md#cold-layer).

## ADR-0025 dec 7

The exporter reads only `sensor_measurements`; no aggregate table is archived.

| Code | Role |
|---|---|
| `services/cold_export/source.py::PostgresMeasurementSource` | Reads raw `sensor_measurements` a day at a time |

| Deferred item | Observable signal | Code it would touch |
|---|---|---|
| Long-horizon aggregate history | A reader needing aggregates older than the aggregate retention | A separate ADR |

## ADR-0025 dec 8

Each day is plain Parquet parts listed in the day's `_manifest.json`; no table format or catalog.

| Code | Role |
|---|---|
| `services/cold_export/store.py::S3ColdStore` | Writes the parts and the manifest |

| Deferred item | Observable signal | Code it would touch |
|---|---|---|
| Table format (Iceberg / Delta / Hudi) | An init-script migration that alters the columns of `tenant_<uuid>.sensor_measurements`; `concurrencyPolicy` in `cronjob-cold-export.yaml` moving off `Forbid`; a second writer to the cold bucket | `ColdStore` adapter (`services/cold_export/store.py`) plus a catalog |

## ADR-0025 dec 9

The export runs as a scheduled job: Helm CronJob (`coldExport.schedule`, default `0 3 * * *`, `concurrencyPolicy: Forbid`); Compose one-shot service under the `cold-export` profile.

| Code | Role |
|---|---|
| `deploy/helm/industryflow/templates/cronjob-cold-export.yaml` | The scheduled export job (Helm) |
| `services/cold_export/main.py` | The job's entry point |

| Deferred item | Observable signal | Code it would touch |
|---|---|---|
| ML-pipeline orchestrator | A real multi-step training pipeline | A separate ADR |

## ADR-0025 dec 11, 12

The exporter is single-node; a distributed export waits for its trigger.

| Deferred item | Observable signal | Code it would touch |
|---|---|---|
| Distributed (Spark) export | Cold-export run duration approaching `coldExport.schedule`; memory near `coldExport.resources.limits.memory`; per-tenant-day row counts in the `Verified cold export: <schema> <day> <part> (N rows)` log line | `MeasurementSource` / `ColdStore` adapters behind `services/cold_export/ports.py`; `exporter.py` unchanged |

The export horizon ([cold layer](data-and-storage.md#cold-layer)) is a setting, not a trigger.
Revisit it on raw hypertable size and node disk (postgres-exporter, node-exporter) and on the window
operators query for forensics.
