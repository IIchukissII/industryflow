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

### Anomaly detectors

A detector turns engineered features (and optionally a model) into an anomaly verdict:

```python
from extensions import register_detector, DetectionResult

@register_detector("rule")
async def rule_detector(features, model, threshold, ctx):
    score = ...  # 0.0–1.0
    return DetectionResult(score=score, is_anomaly=score >= threshold)
```

The ML inference path dispatches a model record's `detector` (default `sklearn`, the
built-in that scores scikit-learn-family models) through the registry — so a domain can
supply a custom or model-free detector without touching the core scorer.

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
