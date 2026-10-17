<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Cold-layer deferred items

The cold-layer items [ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md)
defers to a trigger, the signal that shows each trigger has fired, and the code each would touch.
None is built.

| Item | ADR-0025 | Observable signal | Code it would touch |
|---|---|---|---|
| Table format (Iceberg / Delta / Hudi) | dec 8 | An init-script migration that alters the columns of `tenant_<uuid>.sensor_measurements`; `concurrencyPolicy` in `cronjob-cold-export.yaml` moving off `Forbid`; a second writer to the cold bucket | `ColdStore` adapter (`services/cold_export/store.py`) plus a catalog |
| Distributed (Spark) export | dec 11 | Cold-export run duration approaching `coldExport.schedule`; memory near `coldExport.resources.limits.memory`; per-tenant-day row counts in the `Verified cold export: <schema> <day> <part> (N rows)` log line | `MeasurementSource` / `ColdStore` adapters behind `services/cold_export/ports.py`; `exporter.py` unchanged |
| Long-horizon aggregate history | dec 7 | A reader needing aggregates older than the aggregate retention | A separate ADR |
| ML-pipeline orchestrator | dec 9 | A real multi-step training pipeline | A separate ADR |

The export horizon ([cold layer](data-and-storage.md#cold-layer)) is a setting, not a trigger.
Revisit it on raw hypertable size and node disk (postgres-exporter, node-exporter) and on the window
operators query for forensics.
