<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# tep-reference extension

Example extension that registers Tennessee-Eastman Process (TEP) plugins through the extension contract of [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md) and [ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md), without editing the core.

## Contents

| Path | Content |
|---|---|
| `tep_reference.py` | Module `tep_reference`; targets extension API `0.1.0` |
| `tep_reactor_config.json` | TEP feature configuration (`equipment_type: tep_reactor`) |
| `producer/stream_tep_data.py` | Streams `data/tep_streaming_data.csv` to the ingestion edge over device mTLS |
| `producer/convert_tep_data.py` | Builds the streaming CSV from the TEP `.RData` training files |
| `producer/create_sensors.py` | Builds the sensor payload and mapping in `producer/data/` |
| `producer/data/` | `tep_streaming_data.csv`, `sensors_mapping.json`, `bulk_sensors_payload.json` |

## Registered plugins

| Kind | Name | Behaviour |
|---|---|---|
| Feature transform | `tep_reactor_pressure_margin` | `params.limit_kpa` (default `2950.0`) minus the reading of `sensor` (default `xmeas_7`) |
| Detector | `tep_rule` | Score = largest absolute feature value / 100, clamped to `[0, 1]`; anomaly when score ≥ threshold |

A feature configuration entry uses the transform like a built-in:

```json
{ "name": "reactor_pressure_margin", "type": "tep_reactor_pressure_margin",
  "sensor": "xmeas_7", "params": { "limit_kpa": 2950.0 } }
```

## Loading

The ML service imports each module named in `EXTENSION_MODULES` (comma-separated) at startup; the
module must be on the service's import path. The core never imports `tep_reference` by name.

```sh
EXTENSION_MODULES=tep_reference
```

## Producer settings

`producer/stream_tep_data.py` reads:

| Env var | Default | Meaning |
|---|---|---|
| `INGESTION_URL` | `https://localhost:8443/ingest` | Ingestion edge endpoint |
| `DEVICE_CERT` | none (required) | Device certificate chain from `scripts/device-ca.sh issue` |
| `DEVICE_KEY` | none (required) | Device private key |
| `INGEST_CA` | empty | CA bundle for the edge's server certificate; empty skips verification |
| `EQUIPMENT_ID` | `550e8400-e29b-41d4-a716-446655440100` | Equipment the readings belong to |
| `SITE_ID` | `factory-tep` | Site id in each reading |

`producer/convert_tep_data.py` reads `TEP_FAULT_FREE_FILE` (default
`data/TEP_FaultFree_Training.RData`) and `TEP_FAULTY_FILE` (default `data/TEP_Faulty_Training.RData`).

## Related

| Doc | Topic |
|---|---|
| [extensions.md](../../docs/operations/extensions.md) | Operating extensions |
| [device-mtls.md](../../docs/operations/device-mtls.md) | Issuing device certificates |
