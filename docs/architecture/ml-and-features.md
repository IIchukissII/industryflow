<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ML and feature engineering

Feature configurations, the model registry, inference, detectors and drift in `ml-service-api`.
Code in `services/ml_service/api/` is authoritative. Domain transforms and detectors are
[extensions](../operations/extensions.md).

| Decision | ADR |
|---|---|
| Extension contract and mechanism (transforms, detectors) | [ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md), [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md) |
| Window baselines read from the Spark aggregates; neutral value on a missing baseline | [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md) |
| Stateful-feature kill-switch | [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md) |
| Artifact formats, supported flavor set, skops trust by provenance | [ADR-0027](../../ADR/ADR-0027-model-artifact-supply-chain-parity.md) |
| Score semantics declared, never inferred; detector recorded at registration | [ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) |
| Model tracking and registry per tenant | [ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md), [ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway.md) |
| Drift monitoring | [ADR-0021](../../ADR/ADR-0021-model-drift-monitoring.md) |
| Operator feedback and retrain recommendation | [ADR-0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md), [alerting.md](alerting.md) |
| Externally-authored models | [ADR-0030](../../ADR/ADR-0030-externally-authored-model-admission.md), [model-upload.md](model-upload.md) |

## Components

| Component | Code |
|---|---|
| Feature engine | `feature_engineering/engine.py` |
| Baseline provider (reads `sensor_aggregations_<granularity>`) | `feature_engineering/baseline_provider.py` |
| Kill-switch (`public.platform_config.stateful_features_enabled`) | `feature_engineering/kill_switch.py` |
| Built-in transforms | `extensions/builtins.py` |
| Built-in detectors | `extensions/builtins_detectors.py` |
| Model loading, skops trust | `model_loader.py`, `skops_trust.py`, `model_uri.py` |
| Warm model cache (LRU, `MODEL_CACHE_SIZE`, default 8) | `model_cache.py` |
| Drift (reference profile capture, scoring) | `drift/` |
| Deployment load probe | `load_probe.py`, `load_probe_worker.py` |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST, GET | `/api/feature-configs` | Create, list |
| GET, PUT, DELETE | `/api/feature-configs/{config_id}` | Read, replace, delete |
| GET | `/api/feature-configs/equipment/{equipment_type}` | By equipment type |
| POST, GET | `/api/models` | Register, list |
| GET, DELETE | `/api/models/{model_id}` | Read, delete |
| POST | `/api/models/{model_id}/deploy` | Load-and-score gate |
| GET | `/api/models/{model_id}/download` | Artifact |
| GET | `/api/models/latest/{model_type}` | Latest by type |
| POST | `/api/models/compare` | Compare |
| POST | `/api/models/upload-capability` | Mint an upload capability ([model-upload.md](model-upload.md)) |
| GET | `/api/registered-models`, `/api/registered-models/{name}` | Tenant-scoped MLflow registry read |
| PATCH | `/api/registered-models/{name}` | Edit the description |
| GET | `/api/experiments`, `/api/experiments/{name}/runs` | Tenant-scoped experiment read |
| GET | `/api/ml/capabilities` | Servable flavors and detectors, discovered from the registry |
| POST | `/api/inference/predict` | Score a reading |
| POST | `/api/drift/evaluate` | Data and prediction drift for a model |

## Feature configuration

Stored per tenant in `feature_engineering_configs`: `base_sensors` and an ordered list of
`transformations`, each with a `type` and params. `ml_models.feature_config_id` links a model to its
config.

| Built-in transform | Stateful |
|---|---|
| `identity` | no |
| `polynomial` | no |
| `interaction` | no |
| `deviation` | no |
| `statistical` | yes |
| `rolling_stat` | yes |

Stateful transforms read their baseline window from `params.granularity` (default `1min`, from
`industryflow_contracts.windows`).

## Detectors

`ml_models.detector` names the detector; a model with none is refused.

| Detector | Semantics | Flavors | Score from |
|---|---|---|---|
| `sklearn` | anomaly probability (compatibility name) | `mlflow.sklearn`, `mlflow.xgboost` | Estimator capability, `predict_proba` first |
| `direct` | direct 0–1 score | `mlflow.sklearn`, `mlflow.xgboost` | Model output; outside 0–1 is refused |
| `outlier` | outlier score | `mlflow.sklearn` | `decision_function` |
| `autoencoder` | reconstruction error | `mlflow.sklearn`, `mlflow.pyfunc` | Reconstruction distance |

## Model record (`ml_models`)

| Column | Added by | Meaning |
|---|---|---|
| `feature_config_id` | `06-feature-engineering-tables.sql` | Feature config for inference |
| `reference_profile` (JSONB) | `14-model-drift-reference-profile.sql` | Drift baseline; absent → drift unavailable |
| `compatibility_status`, `compatibility_detail`, `compatibility_checked_at` | `18-model-compatibility-status.sql` | Training vs serving environment verdict, set at registration and deployment; null = never evaluated |
| `provenance`, `artifact_uri` | `19-model-provenance.sql` | Kernel-trained (by run) or uploaded (by admitted location) |
| `detector` | `20-model-detector.sql` | Detector for serving |

## Inference (`POST /api/inference/predict`)

| Step | Detail |
|---|---|
| Tenant | From the verified identity, or the `company_id` body field with `X-Internal-Service-Token` |
| Load | Model record and feature config; model from the warm cache or a cold load off the event loop |
| Address check | A run must belong to one of the tenant's experiments; an artifact must be an admitted upload under the tenant's prefix; re-checked on every call |
| Features | Feature config applied; stateful slots neutralised when the kill-switch is off |
| Score | Detector named by the record; response `degraded: true` when any feature was neutralised |

Kernel-trained tree-based sklearn models need the skops grant at log time:
`mlflow.sklearn.log_model(model, name="model", skops_trusted_types=industryflow.SKOPS_TRUSTED_TYPES)`.

## Drift (`POST /api/drift/evaluate`)

| Item | Detail |
|---|---|
| Baseline | `ml_models.reference_profile`, captured at registration by `drift/reference_profile.py` |
| Current window | Trailing window of the tenant's data, aligned to instants where every compared sensor reported |
| Engine | `evidently` |
| Output | Data drift (drifted-feature share) and prediction drift; `prediction_drift_reason` when prediction drift cannot be computed |
| Prediction column | Must hold 0–1 detector scores; anything else is refused at registration |
| Caller | alert-service-detector `statistical` rules ([alerting.md](alerting.md)) |

Model training is manual, in notebooks ([notebooks.md](notebooks.md)); no training service exists.
