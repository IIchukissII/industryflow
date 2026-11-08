<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Reference extension

The manual of [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md), [ADR-0008](../../ADR/ADR-0008-extension-interface-for-a-domain-generic-core.md): the extension that demonstrates the boundary with a fictional pump, registered through the extension contract without editing the core.

## ADR-0010 dec 5

One domain configuration, one transform and one detector.

| Path | Content |
|---|---|
| `reference_extension.py` | Module `reference_extension`; targets extension API `0.5.0` |
| `reference_config.json` | Feature configuration, `equipment_type: example_pump` |

### Registered plugins

| Kind | Name | Behaviour |
|---|---|---|
| Feature transform | `reference_pressure_margin` | `params.limit` minus the reading of `sensor`; a definition naming neither is refused ([ADR-0044](../../ADR/ADR-0044-a-model-is-scored-on-complete-computed-input-or-not-at-all.md) dec 2) |
| Detector | `reference_margin_rule` | Model-free, `direct_score`: the `pressure_margin` feature scored 0 at 2 bar of headroom or more, 1 at or past the limit, linear between; features without it are refused ([ADR-0028](../../ADR/ADR-0028-declared-score-semantics-and-the-model-adapter-contract.md) dec 2) |

### The pump

| Sensor | Range for the sample device |
|---|---|
| `discharge_pressure` | `6`–`9` (bar; the configuration's limit is `10`) |
| `flow_rate` | `30`–`50` |
| `motor_temperature` | `50`–`70` |

Streaming it: [getting started, step 5](../../docs/getting-started.md#5-stream-test-data).

The code and its proofs: [ADR-0010 dec 5](../../docs/operations/extensions.md#adr-0010-dec-5).

## ADR-0008 dec 6

The package depends on the platform and is imported only when configured:
`EXTENSION_MODULES=reference_extension`, with this directory on `ml-service-api`'s import path
([loading an extension](../../docs/operations/extensions.md#loading-an-extension)).
