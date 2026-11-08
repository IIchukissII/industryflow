<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Reference extension

The extension that demonstrates the boundary of [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)
dec 5 and [ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md) dec 6: one domain
configuration, one transform and one detector, registered through the extension contract without
editing the core. The domain is a fictional pump.

| Path | Content |
|---|---|
| `reference_extension.py` | Module `reference_extension`; targets extension API `0.5.0` |
| `reference_config.json` | Feature configuration, `equipment_type: example_pump` |

## Registered plugins

| Kind | Name | Behaviour |
|---|---|---|
| Feature transform | `reference_pressure_margin` | `params.limit` minus the reading of `sensor`; a definition naming neither is refused ([ADR-0044](../../ADR/ADR-0044-complete-model-input.md) dec 2) |
| Detector | `reference_margin_rule` | Model-free, `direct_score`: the `pressure_margin` feature scored 0 at 2 bar of headroom or more, 1 at or past the limit, linear between; features without it are refused ([ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) dec 2) |

## The pump

| Sensor | Range for the sample device |
|---|---|
| `discharge_pressure` | `6`–`9` (bar; the configuration's limit is `10`) |
| `flow_rate` | `30`–`50` |
| `motor_temperature` | `50`–`70` |

Streaming it: [getting started, step 5](../../docs/getting-started.md#5-stream-test-data).

## Loading

`EXTENSION_MODULES=reference_extension`, with this directory on `ml-service-api`'s import path
([loading an extension](../../docs/operations/extensions.md#loading-an-extension)).

The code and its proofs: [ADR-0010 dec 5](../../docs/operations/extensions.md#adr-0010-dec-5).
