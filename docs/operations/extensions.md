<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Extensions and plugins

The manual of [ADR-0008](../../ADR/ADR-0008-extension-interface-for-a-domain-generic-core.md), [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md): the extension surfaces of `ml-service-api`, the feature-transform and detector plugin contracts, and loading an extension module.

## ADR-0008 dec 1

The core defines the plugin contracts in one package; an extension registers into them and edits no core file. Core rules name no framework or domain.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py` | The contracts: registries, decorators, context and result types |
| `services/notebook_tracking_gateway/tests/test_admission.py::test_admits_a_safe_format_the_rule_has_never_heard_of` | Proof: the structural admission rule names no framework |

The core ships only generic built-ins and never imports a named extension.

## ADR-0008 dec 2

| Surface | Content | Change needed |
|---|---|---|
| Configuration | Feature definitions (`feature_engineering_configs`), sensor mappings, alert rules, per tenant | Data only |

Feature definitions and alert rules are per-tenant rows written through the API; adding one changes
no code.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/feature_configs.py` | `/api/feature-configs`: feature definitions |
| `infrastructure/timescaledb/init-scripts/06-feature-engineering-tables.sql` | `feature_engineering_configs` per tenant |
| `services/api_gateway/routers/alert_rules.py` | `/api/alert-rules`: alert rules |
| `infrastructure/timescaledb/init-scripts/02-tenant-alert-tables.sql` | `alert_rules` per tenant |

## ADR-0008 dec 3

| Surface | Content | Change needed |
|---|---|---|
| Code plugins | Feature transforms and detectors registered through `extensions` (`services/ml_service/api/extensions/__init__.py`) | An extension module named in `EXTENSION_MODULES` |

| Registry | Decorator |
|---|---|
| Feature transforms | `register_transform(name, *, check, stateful=False, neutral=0.0)` |
| Detectors | `register_detector(name, *, semantics=DIRECT_SCORE, handles_flavors=None)` |

Built-ins: [transforms](../architecture/ml-and-features.md#adr-0023-dec-3),
[detectors](../architecture/ml-and-features.md#detectors).
Registering a different function under an existing name raises `ValueError`.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::register_transform` | Registers a feature transform |
| `services/ml_service/api/extensions/__init__.py::register_detector` | Registers a detector |

## ADR-0008 dec 4

Not yet realised.

Domain data goes in extension-owned tables in the tenant schema, with no columns on core tables. No extension ships tables; their registration and migration are deferred in ADR-0008.

## ADR-0008 dec 5

The module calls `check_api_version("<target>")` at import; the rule is [ADR-0010 dec 3](#adr-0010-dec-3).

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::check_api_version` | Accepts or refuses a target version |
| `extensions/reference/reference_extension.py` | Declares its target, `0.5.0` |

## ADR-0008 dec 6

The reference extension is a separate package that depends on the platform.

| Code | Role |
|---|---|
| `extensions/reference/reference_extension.py` | Reference extension |
| `services/ml_service/tests/test_extension_contract.py::test_the_platform_loads_no_extension_it_is_not_configured_to_load` | Proof: not loaded unless configured |
| `services/ml_service/tests/test_extension_contract.py::test_no_platform_source_names_the_reference_module` | Proof: no platform source imports it |

Extensions depend on IndustryFlow; the platform never vendors or imports them.

### Example

[`extensions/reference/reference_extension.py`](../../extensions/reference/reference_extension.py) ([README](../../extensions/reference/README.md)) is
loaded only when `EXTENSION_MODULES` names it.

## ADR-0008 dec 7

Not yet realised.

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

### Loading an extension

```bash
EXTENSION_MODULES=my_domain.transforms,my_domain.detectors
```

| Rule | Detail |
|---|---|
| Loading | `ml-service-api` imports each comma-separated module at startup, which runs its `@register_*` decorators |
| Import path | The module must be importable by the service (a separate package depending on IndustryFlow) |

## ADR-0010 dec 2

A transform is `async (transformation, sensor_data, ctx) -> float`. The feature engine looks up each configured `type` in the registry and calls it; an unknown type is refused when the configuration is written and when an engine is built from it. The generic transforms register on package import.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::TransformFn` | The contract signature |
| `services/ml_service/api/extensions/__init__.py::TransformContext` | The runtime context |
| `services/ml_service/api/extensions/builtins.py` | Built-ins: `identity`, `polynomial`, `interaction`, `deviation`, `statistical` |
| `services/ml_service/api/feature_engineering/engine.py` | Dispatch by `type` through `get_transform`; each definition's `check_definition` at build |
| `services/ml_service/api/extensions/__init__.py::check_definition` | Runs the registered transform's check |
| `services/ml_service/tests/test_unknown_transform.py` | Proof: an unknown type, and a definition its transform cannot compute, are refused where written and at build |

### Feature transforms

A transform is `async (transformation, sensor_data, ctx) -> float`. The feature engine dispatches each
configured transform `type` through the registry. `ctx` is a `TransformContext` (`baseline_provider`,
`equipment_id`, `company_id`).

`check` is `(transformation) -> None`, raising `InvalidFeature` for a definition the transform cannot
compute. It runs where a configuration is written (`422`) and when an engine is built (`422` at
`/api/inference/predict`), before any reading and whatever the kill-switch says.

```python
import math

from extensions import InvalidFeature, StatefulUnavailable, register_transform, sensor_value
from industryflow_contracts import windows

def check_margin(transformation):
    limit = (transformation.get("params") or {}).get("limit")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit):
        raise InvalidFeature(transformation.get("name"), "it names no finite numeric params.limit")
    if not isinstance(transformation.get("sensor"), str):
        raise InvalidFeature(transformation.get("name"), "it names no sensor")

@register_transform("pressure_margin", check=check_margin)
async def pressure_margin(transformation, sensor_data, ctx):
    return transformation["params"]["limit"] - sensor_value(sensor_data, transformation["sensor"])

def check_baseline(transformation):
    if not isinstance(transformation.get("sensor"), str):
        raise InvalidFeature(transformation.get("name"), "it names no sensor")
    if (transformation.get("params") or {}).get("granularity") not in windows.WINDOWS:
        raise InvalidFeature(transformation.get("name"), "it names no window")

@register_transform("pressure_baseline", check=check_baseline, stateful=True, neutral=0.0)
async def pressure_baseline(transformation, sensor_data, ctx):
    baseline = await ctx.baseline_provider.compute_baseline(
        ctx.company_id, ctx.equipment_id, transformation["sensor"], transformation["params"]["granularity"])
    if baseline is None:
        raise StatefulUnavailable("no closed window yet")
    return baseline["mean"]
```

A transform never returns a substitute value ([ADR-0044](../../ADR/ADR-0044-a-model-is-scored-on-complete-computed-input-or-not-at-all.md)):

| SDK name | Use | Result at `/api/inference/predict` |
|---|---|---|
| `sensor_value(sensor_data, name)` | The only way to read a sensor; raises `MissingSensors` for an absent or non-finite value | `422` naming the sensors |
| `InvalidFeature(feature, reason)` | The definition cannot be interpreted (missing or unknown parameter) | `422` |
| `StatefulUnavailable(reason)` | A `stateful=True` transform's store cannot supply the value | Slot takes `neutral`; `degraded: true`, named in `neutralized_features`. Drift and the deployment gate take no substitute: prediction drift and its baseline are not computed, with the reason |
| A computed value that is not a finite number | — | `500`; the score fails |
| Any other exception | — | `500`; the score fails |

The engine refuses a reading lacking any `base_sensors` entry, or any sensor a feature names in
`sensor` / `sensors`, before a transform runs; with a `stateful=True` feature in the configuration, a
reading that names no valid `equipment_id` is refused too (`422`), whatever the kill-switch says.

`ctx.baseline_provider.compute_baseline(company_id, equipment_id, sensor, granularity)` returns
`{"mean", "std"}`, `None`, or raises, as the provider's answers in
[ML and features](../architecture/ml-and-features.md#adr-0023-dec-7) say; the window is required and must
be one declared in `industryflow_contracts.windows`, and a window or ids it cannot read raise
`ValueError`. When drift scores a stored reading, `ctx.baseline_provider` reads the window
written by that reading's instant (extension API 0.6.0).

| Tag | Meaning |
|---|---|
| `stateful=True` | The transform reads an external store. Required for any such transform ([ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) |
| `neutral` | Value the engine puts in the slot while the stateful-feature kill-switch is off (the transform is then not called), or when the transform raises `StatefulUnavailable`. The vector keeps its length and order |

Operating the kill-switch: [monitoring](monitoring.md#runbook-stateful-feature-kill-switch).

## ADR-0010 dec 3

Each contract carries the extension-API version a plugin declares against.

| Rule | Detail |
|---|---|
| Version check | The module calls `check_api_version("<target>")` at import. It passes when the major matches `EXTENSION_API_VERSION` and the platform minor is at least the target's; otherwise `ValueError` |
| `EXTENSION_API_VERSION` | Defined in `services/ml_service/api/extensions/__init__.py`; stability rules in [ADR-0010 dec 3](../../ADR/ADR-0010-extension-plugin-mechanism.md) |

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::EXTENSION_API_VERSION` | The current extension-API version |
| `services/ml_service/api/extensions/__init__.py::check_api_version` | The compatibility rule |
| `services/ml_service/tests/test_extension_contract.py::test_an_extension_is_loaded_only_against_a_compatible_api` | Proof: the version rule |

## ADR-0010 dec 4

`load_extension_modules` imports each configured module into the `ml-service-api` process; its
plugins register into module-level registries and run as in-process calls. No out-of-process plugin
transport exists.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::load_extension_modules` | Imports the configured modules in process |
| `services/ml_service/api/extensions/__init__.py::_TRANSFORMS` | In-process transform registry |
| `services/ml_service/api/extensions/__init__.py::_DETECTORS` | In-process detector registry |

## ADR-0010 dec 5

| Part | File |
|---|---|
| Domain configuration | `extensions/reference/reference_config.json` |
| Transform | `reference_pressure_margin` |
| Detector | `reference_margin_rule` (model-free) |

The module declares its target extension-API version and is imported only when `EXTENSION_MODULES` names `reference_extension`. No platform code imports it.

| Code | Role |
|---|---|
| `extensions/reference/reference_extension.py` | Reference transform and detector |
| `extensions/reference/reference_config.json` | Reference domain configuration |
| `services/ml_service/tests/test_extension_contract.py` | Proof: discovery by configured import, registration, the configuration accepted only when loaded, computation, dispatch by name, the version rule |

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
| `services/ml_service/api/scoring.py` | The one owner of scoring: resolves the record's detector and builds its context |
| `services/ml_service/tests/test_scoring.py` | Proof: the context reaches the detector; only `scoring` builds one or resolves a detector |
| `services/ml_service/api/extensions/__init__.py::DetectionResult` | The detection result |
| `services/ml_service/api/extensions/builtins_detectors.py` | Built-in detectors |
| `services/ml_service/api/routers/models.py::_kernel_detector_or_refuse` | Default `sklearn` at registration; refuses an unloaded or flavor-mismatched detector |
| `services/ml_service/api/scoring.py::detector_name` | Resolves the record's detector; refuses a record without one, or naming one not loaded |
| `services/ml_service/api/repository.py` | Writes and reads the `detector` column |
| `infrastructure/timescaledb/init-scripts/20-model-detector.sql` | Adds the `detector` column per tenant; backfills kernel rows with `sklearn` |
| `services/ml_service/tests/test_inference_dispatch.py::test_the_model_is_scored_by_the_detector_its_record_names` | Proof: dispatch by the record |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_record_with_no_detector_is_refused_not_defaulted` | Proof: no serving default |
| `services/ml_service/tests/test_registration_gate_uploaded.py::test_a_kernel_model_defaults_to_the_compatibility_detector` | Proof: registration default |
| `services/ml_service/tests/test_repository_model_columns.py::test_the_detector_round_trips` | Proof: the column round-trips |

### Detectors

A detector is `async (features, model, threshold, ctx) -> DetectionResult`; `model` is `None` for a
model-free detector.
`ctx` is a `DetectorContext`: `feature_names` (the configuration's feature order), `equipment_id`,
`reconstruction_scale` (from the model's training metrics). Inference, drift, the deploy gate and the
load probe all score through one owner, so a detector meets the same context on each.

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
| `semantics` | What the model's output means: `ANOMALY_PROBABILITY`, `OUTLIER_SCORE`, `RECONSTRUCTION_ERROR`, `DIRECT_SCORE` ([ADR-0028](../../ADR/ADR-0028-declared-score-semantics-and-the-model-adapter-contract.md)) |
| `handles_flavors` | MLflow flavors the detector can score; empty claims none |
| `UninterpretableModel` | Raise it when the meaning of a model's output cannot be established |
| Dispatch | Inference uses the model record's `detector`; a record without one is refused (422) |
| `GET /api/ml/capabilities` | Every registered detector with its semantics and flavors, read from the registry, including those from `EXTENSION_MODULES` |
