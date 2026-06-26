<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# tep-reference — example IndustryFlow extension

A minimal **reference extension** demonstrating the plugin contract from
**[ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)** (which makes
**[ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md)** concrete). It exists to
show — and test — that a domain extends IndustryFlow *without editing the core*. The real
reference extension is IndustryGrow, in its own repository.

It contains:

- `tep_reference.py` — Tennessee-Eastman domain plugins (a feature transform
  `tep_reactor_pressure_margin` and a detector `tep_rule`) registered through the platform
  contracts.
- `tep_reactor_config.json` — the TEP feature definition that previously lived inside
  `ml_service`; a domain artifact now owned by the extension (ADR-0008 dec 5/7).
- `producer/` — the reference **data producer**: streams the Tennessee-Eastman dataset to
  the ingestion edge over device mTLS (`stream_tep_data.py`), plus the dataset and the
  helpers that built it (`convert_tep_data.py`, `create_sensors.py`, `data/`). This is
  reference/test tooling for the example dataset, not part of the platform core.

## How the platform loads it

The core ships only generic transforms. To make a domain transform available, name the module
in `EXTENSION_MODULES` (comma-separated) and put it on the service's import path:

```sh
EXTENSION_MODULES=tep_reference
```

At startup the ML service imports each named module, which runs its `@register_transform`
decorators. A configuration entry then uses the new type like any built-in:

```json
{ "name": "reactor_pressure_margin", "type": "tep_reactor_pressure_margin",
  "sensor": "xmeas_7", "params": { "limit_kpa": 2950.0 } }
```

The platform never imports this module by name itself — it loads only what it is configured to
load, so the dependency points one way (extension → platform).
