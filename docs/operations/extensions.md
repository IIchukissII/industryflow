<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Extensions & plugins

IndustryFlow is a **domain-generic** platform: the core carries no domain specifics, and a
domain (cultivation, manufacturing, energy, …) extends it through stable contracts rather
than by editing core code (**[ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md)**,
made concrete in **[ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)**).

There are two extension surfaces:

- **Configuration** — the primary surface. Feature definitions, sensor mappings, and alert
  rules are data the platform loads per tenant (e.g. each ML model points at a
  `feature_engineering_configs` row); no code change is needed to add them.
- **Code plugins** — for behaviour configuration can't express. The platform exposes
  **versioned plugin contracts**; a domain registers implementations in its own module and
  the platform loads them. The core never imports a named extension.

## The plugin contracts

Both contracts use the same registry pattern. A plugin is registered with a decorator and
resolved by name at runtime; the core ships only generic, domain-free built-ins.

### Feature transforms

A transform turns raw sensor readings into one engineered feature:

```python
from extensions import register_transform

@register_transform("pressure_margin")
async def pressure_margin(transformation, sensor_data, ctx):
    params = transformation.get("params", {})
    limit = params.get("limit", 0.0)
    return limit - sensor_data.get(transformation["sensor"], 0.0)
```

The feature engine dispatches each configured transform `type` through the registry. Built-in
generic transforms: `identity`, `polynomial`, `interaction`, `deviation`, `statistical`,
`rolling_stat`.

**Declare a transform `stateful` if it reads an external store** (ADR-0024) — as opposed to being a
pure function of the reading it is handed:

```python
@register_transform("pressure_baseline", stateful=True, neutral=0.0)
async def pressure_baseline(transformation, sensor_data, ctx):
    return await ctx.baseline_provider.compute_rolling_mean(...)
```

The tag is what lets the **stateful-feature kill-switch** neutralize your transform as a class when
its substrate is degraded: with the switch off, the engine fills the slot with `neutral` and does
**not call the transform at all**, so the degraded store stops being queried. The feature vector
keeps its length and order, so bound models keep serving. A transform that reads an external store
but does not declare `stateful=True` will keep hammering that store during an incident the operator
believes they have contained — so declare it. Both keywords are optional (`stateful=False`,
`neutral=0.0`), so transforms written before this existed keep working unchanged.

See [monitoring.md](monitoring.md) for flipping the switch and seeing that it took effect.

### Anomaly detectors

A detector turns engineered features (and optionally a model) into an anomaly verdict:

```python
from extensions import register_detector, DetectionResult, RECONSTRUCTION_ERROR

@register_detector(
    "rule",
    semantics=RECONSTRUCTION_ERROR,          # what your score MEANS (ADR-0028)
    handles_flavors=["mlflow.sklearn"],      # which artifacts you can score
)
async def rule_detector(features, model, threshold, ctx):
    score = ...  # 0.0–1.0
    return DetectionResult(score=score, is_anomaly=score >= threshold)
```

The ML inference path dispatches a model record's `detector` (default `sklearn`) through the
registry — so a domain can supply a custom or model-free detector without touching the core
scorer.

**Declare your semantics; do not let the platform guess them**
([ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md)). The meaning of a
model's output is not recoverable from the output: `predict() == 1` means *normal* to an
IsolationForest and *anomaly* to an XGBoost classifier. The platform used to sniff it, and scored
every IsolationForest reading as an anomaly for months (#236). If your detector cannot establish what
a model's output means, raise `UninterpretableModel` — a refusal is legible, a wrong score is not.

`GET /api/ml/capabilities` lists every registered detector, its semantics, and the flavors it claims —
including the ones your `EXTENSION_MODULES` added. It is discovered from the registry, so it cannot
drift from what is actually loaded.

## Loading an extension

Name the importable module(s) in `EXTENSION_MODULES` (comma-separated); the ML service
imports them at startup, which runs their `@register_*` decorators:

```bash
EXTENSION_MODULES=my_domain.transforms,my_domain.detectors
```

The module must be on the service's import path (its own package depending on IndustryFlow).
Each extension declares the platform **extension-API version** it targets; the loader checks
compatibility (major must match) and refuses a mismatch.

## Versioning, boundaries, packaging

- The platform publishes `EXTENSION_API_VERSION` (semver). A breaking contract change is a
  major bump, never silent.
- Domain data lives in **extension-owned tables** inside the per-tenant schema, not as columns
  on core tables — the core's generic entities (equipment, sensor, measurement) stay unchanged.
- Extensions are **separate packages** that depend on IndustryFlow; the platform never vendors
  or imports them. The dependency points one way.

## Example

`extensions/tep-reference/` is a small **example** extension (the Tennessee-Eastman reference
dataset) showing a domain transform and detector registered through these contracts. It is
illustrative only — the platform does not load it unless `EXTENSION_MODULES` names it.
