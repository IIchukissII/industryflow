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
| Code plugins | Feature transforms and detectors registered through `extensions` (`services/ml_service/api/extensions/`) (ADR-0008 dec 3) | An extension module named in `EXTENSION_MODULES` |

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
| `EXTENSION_API_VERSION` | Defined in `services/ml_service/api/extensions/__init__.py`; stability rules in [ADR-0010 dec 3](../../ADR/ADR-0010-extension-plugin-mechanism.md) |
| Domain data | Extension-owned tables in the tenant schema; no columns on core tables |
| Dependency direction | Extensions depend on IndustryFlow; the platform never vendors or imports them |

## Example

[`extensions/tep-reference/`](../../extensions/tep-reference/README.md) (module `tep_reference`) is
loaded only when `EXTENSION_MODULES` names it.

## ADR-0008 dec 1

The core defines the plugin contracts in one package; an extension registers into them and edits no core file. Core rules name no framework or domain.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py` | The contracts: registries, decorators, context and result types |
| `services/notebook_tracking_gateway/tests/test_admission.py::test_admits_a_safe_format_the_rule_has_never_heard_of` | Proof: the structural admission rule names no framework |

## ADR-0008 dec 6

The reference extension is a separate package that depends on the platform.

| Code | Role |
|---|---|
| `extensions/tep-reference` | Reference extension and data producer |

## ADR-0010 dec 1

| Step | Mechanism |
|---|---|
| Discovery | At startup the service imports each module named in `EXTENSION_MODULES` |
| Registration | The module's `@register_transform` / `@register_detector` decorators write into the in-process registries |
| Duplicate | A different function under a registered name raises `ValueError` |
| Lookup | The feature engine and inference resolve a name through `get_transform` / `get_detector` |

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::load_extension_modules` | Imports the configured modules |
| `services/ml_service/api/extensions/__init__.py::register_transform` | Transform registry; refuses a second function under one name |
| `services/ml_service/api/extensions/__init__.py::register_detector` | Detector registry; refuses a second function under one name |
| `services/ml_service/api/main.py` | Loads `EXTENSION_MODULES` at startup |
| `services/ml_service/api/config.py::EXTENSION_MODULES` | The configured module list |

## ADR-0010 dec 2

A transform is `async (transformation, sensor_data, ctx) -> float`. The feature engine looks up each configured `type` in the registry and calls it; an unknown type is refused when the configuration is written and when an engine is built from it. The generic transforms register on package import.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::TransformFn` | The contract signature |
| `services/ml_service/api/extensions/__init__.py::TransformContext` | The runtime context |
| `services/ml_service/api/extensions/builtins.py` | Built-ins: `identity`, `polynomial`, `interaction`, `deviation`, `statistical` |
| `services/ml_service/api/feature_engineering/engine.py` | Dispatch by `type` through `get_transform` |
| `services/ml_service/tests/test_unknown_transform.py` | Proof: an unknown type is refused |

## ADR-0010 dec 3

Each contract carries the extension-API version a plugin declares against.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions` | Registry, contracts and the current extension-API version |

## ADR-0010 dec 5

| Part | File |
|---|---|
| Domain configuration | `extensions/tep-reference/tep_reactor_config.json` |
| Transform | `tep_reactor_pressure_margin` |
| Detector | `tep_rule` (model-free) |

The module declares its target extension-API version and is imported only when `EXTENSION_MODULES` names `tep_reference`. No platform code imports it.

| Code | Role |
|---|---|
| `extensions/tep-reference/tep_reference.py` | Reference transform and detector |
| `extensions/tep-reference/tep_reactor_config.json` | Reference domain configuration |

## ADR-0010 dec 6

A detector is `async (features, model, threshold, ctx) -> DetectionResult`. Each model record carries a `detector` column; inference dispatches the detector it names.

| Case | Result |
|---|---|
| Kernel-authored model registered without a detector | Recorded as `sklearn`, the generic built-in |
| Uploaded model | Recorded as the detector matching its declared semantics (ADR-0028) |
| Record with no detector at inference | Refused with 422; no default at serving |
| Record naming a detector not loaded | 500 |

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::DetectorContext` | The runtime context |
| `services/ml_service/api/extensions/__init__.py::DetectionResult` | The detection result |
| `services/ml_service/api/extensions/builtins_detectors.py` | Built-in detectors |
| `services/ml_service/api/routers/models.py::_kernel_detector_or_refuse` | Default `sklearn` at registration; refuses an unloaded or flavor-mismatched detector |
| `services/ml_service/api/routers/inference.py::detector_for` | Resolves the record's detector; refuses a record without one |
| `services/ml_service/api/repository.py` | Writes and reads the `detector` column |
| `infrastructure/timescaledb/init-scripts/20-model-detector.sql` | Adds the `detector` column per tenant; backfills kernel rows with `sklearn` |
| `services/ml_service/tests/test_inference_dispatch.py::test_the_model_is_scored_by_the_detector_its_record_names` | Proof: dispatch by the record |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_record_with_no_detector_is_refused_not_defaulted` | Proof: no serving default |
| `services/ml_service/tests/test_registration_gate_uploaded.py::test_a_kernel_model_defaults_to_the_compatibility_detector` | Proof: registration default |
| `services/ml_service/tests/test_repository_model_columns.py::test_the_detector_round_trips` | Proof: the column round-trips |
