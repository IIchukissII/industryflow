<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Extensions and plugins

The extension surfaces of `ml-service-api`: configuration, the feature-transform and detector plugin
contracts, and loading an extension module
([ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md),
[ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)).

| Surface | Content | Change needed |
|---|---|---|
| Configuration | Feature definitions (`feature_engineering_configs`), sensor mappings, alert rules, per tenant | Data only |
| Code plugins | Feature transforms and detectors registered through `extensions` (`services/ml_service/api/extensions/`) | An extension module named in `EXTENSION_MODULES` |

The core ships only generic built-ins and never imports a named extension.

| Registry | Decorator |
|---|---|
| Feature transforms | `register_transform(name, *, stateful=False, neutral=0.0)` |
| Detectors | `register_detector(name, *, semantics=DIRECT_SCORE, handles_flavors=None)` |

Built-ins: [transforms](../architecture/ml-and-features.md#feature-configuration),
[detectors](../architecture/ml-and-features.md#detectors).
Registering a different function under an existing name raises `ValueError`.

## Feature transforms

A transform is `async (transformation, sensor_data, ctx) -> float`. The feature engine dispatches each
configured transform `type` through the registry. `ctx` is a `TransformContext` (`baseline_provider`,
`equipment_id`, `company_id`).

```python
from extensions import InvalidFeature, StatefulUnavailable, register_transform, sensor_value

@register_transform("pressure_margin")
async def pressure_margin(transformation, sensor_data, ctx):
    if "limit" not in transformation.get("params", {}):
        raise InvalidFeature(transformation.get("name"), "it names no limit")
    return transformation["params"]["limit"] - sensor_value(sensor_data, transformation["sensor"])

@register_transform("pressure_baseline", stateful=True, neutral=0.0)
async def pressure_baseline(transformation, sensor_data, ctx):
    baseline = await ctx.baseline_provider.compute_baseline(...)
    if baseline is None:
        raise StatefulUnavailable("no closed window yet")
    return baseline["mean"]
```

A transform never returns a substitute value ([ADR-0044](../../ADR/ADR-0044-complete-model-input.md)):

| SDK name | Use | Result at `/api/inference/predict` |
|---|---|---|
| `sensor_value(sensor_data, name)` | The only way to read a sensor; raises `MissingSensors` for an absent or non-finite value | `422` naming the sensors |
| `InvalidFeature(feature, reason)` | The definition cannot be interpreted (missing or unknown parameter) | `422` |
| `StatefulUnavailable(reason)` | A `stateful=True` transform's store cannot supply the value | Slot takes `neutral`; `degraded: true`, named in `neutralized_features` |
| Any other exception | — | `500`; the score fails |

The engine refuses a reading lacking any `base_sensors` entry, or any sensor a feature names in
`sensor` / `sensors`, before a transform runs.

| Tag | Meaning |
|---|---|
| `stateful=True` | The transform reads an external store. Required for any such transform ([ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) |
| `neutral` | Value the engine puts in the slot while the stateful-feature kill-switch is off (the transform is then not called), or when the transform raises `StatefulUnavailable`. The vector keeps its length and order |

Operating the kill-switch: [monitoring](monitoring.md#runbook-stateful-feature-kill-switch).

## Detectors

A detector is `async (features, model, threshold, ctx) -> DetectionResult`; `model` is `None` for a
model-free detector.

```python
from extensions import register_detector, DetectionResult, RECONSTRUCTION_ERROR

@register_detector(
    "rule",
    semantics=RECONSTRUCTION_ERROR,
    handles_flavors=["mlflow.sklearn"],
)
async def rule_detector(features, model, threshold, ctx):
    score = ...  # 0.0-1.0
    return DetectionResult(score=score, is_anomaly=score >= threshold)
```

| Element | Rule |
|---|---|
| `semantics` | What the model's output means: `ANOMALY_PROBABILITY`, `OUTLIER_SCORE`, `RECONSTRUCTION_ERROR`, `DIRECT_SCORE` ([ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md)) |
| `handles_flavors` | MLflow flavors the detector can score; empty claims none |
| `UninterpretableModel` | Raise it when the meaning of a model's output cannot be established |
| Dispatch | Inference uses the model record's `detector`; a record without one is refused (422) |
| `GET /api/ml/capabilities` | Every registered detector with its semantics and flavors, read from the registry, including those from `EXTENSION_MODULES` |

## Loading an extension

```bash
EXTENSION_MODULES=my_domain.transforms,my_domain.detectors
```

| Rule | Detail |
|---|---|
| Loading | `ml-service-api` imports each comma-separated module at startup, which runs its `@register_*` decorators |
| Import path | The module must be importable by the service (a separate package depending on IndustryFlow) |
| Version check | The module calls `check_api_version("<target>")` at import. It passes when the major matches `EXTENSION_API_VERSION` and the platform minor is at least the target's; otherwise `ValueError` |
| `EXTENSION_API_VERSION` | Defined in `services/ml_service/api/extensions/__init__.py`; stability rules in [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md) dec 3 |
| Domain data | Extension-owned tables in the tenant schema; no columns on core tables |
| Dependency direction | Extensions depend on IndustryFlow; the platform never vendors or imports them |

## Example

[`extensions/tep-reference/`](../../extensions/tep-reference/README.md) (module `tep_reference`) is
loaded only when `EXTENSION_MODULES` names it.
