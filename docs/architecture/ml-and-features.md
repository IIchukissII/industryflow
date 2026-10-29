<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ML and feature engineering

Feature configurations, the model registry, inference, detectors and drift in `ml-service-api`
(port 8002). Domain transforms and detectors are [extensions](../operations/extensions.md).

| Decision | ADR |
|---|---|
| Extension contract and mechanism (transforms, detectors) | [ADR-0008](../../ADR/ADR-0008-extension-and-plugin-interface.md), [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md) |
| Window baselines read from the Spark aggregates; neutral value on a missing baseline | [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md) |
| Stateful-feature kill-switch | [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md) |
| Artifact formats, supported flavor set, skops trust by provenance | [ADR-0027](../../ADR/ADR-0027-model-artifact-supply-chain-parity.md) |
| Out-of-process runtime for torch/GPU (not built) | [ADR-0036](../../ADR/ADR-0036-out-of-process-model-runtime.md) |
| Score semantics declared, never inferred; detector recorded at registration | [ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) |
| Model tracking and registry per tenant | [ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md), [ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway.md) |
| Drift monitoring | [ADR-0021](../../ADR/ADR-0021-model-drift-monitoring.md); baseline [ADR-0035](../../ADR/ADR-0035-drift-baseline-derived-scores.md) |
| Operator feedback and retrain recommendation | [ADR-0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md), [alerting](alerting.md) |
| Externally-authored models | [ADR-0030](../../ADR/ADR-0030-externally-authored-model-admission.md), [model upload](model-upload.md) |

## Code

Paths under `services/ml_service/api/`.

| Module | Path |
|---|---|
| Feature engine | `feature_engineering/engine.py` |
| Baseline provider (reads `sensor_aggregations_<granularity>`) | `feature_engineering/baseline_provider.py` |
| Kill-switch (`public.platform_config.stateful_features_enabled`) | `feature_engineering/kill_switch.py` |
| Built-in transforms | `extensions/builtins.py` |
| Built-in detectors | `extensions/builtins_detectors.py` |
| Model loading, skops trust | `model_loader.py`, `skops_trust.py`, `model_uri.py` |
| Warm model cache (LRU, `MODEL_CACHE_SIZE`, default 8) | `model_cache.py` |
| Drift (reference profile format, scoring) | `drift/` |
| Deployment load probe | `load_probe.py`, `load_probe_worker.py` |

## Endpoints

Auth "token" is an access token (`if_access` cookie or bearer); "writer" is a token whose account
has role `engineer` or `admin`, or is a superuser, else **403**
([roles](../operations/user-management.md#roles)); "internal" is the internal service token
([authentication](../operations/authentication.md)).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/feature-configs` | token | List |
| POST | `/api/feature-configs` | writer | Create |
| GET | `/api/feature-configs/{config_id}` | token | Read |
| PUT, DELETE | `/api/feature-configs/{config_id}` | writer | Replace, delete; a config a model binds keeps `base_sensors` and `transformations` and is not deleted: **409** naming the models ([ADR-0041](../../ADR/ADR-0041-bound-feature-configuration.md)) |
| GET | `/api/feature-configs/equipment/{equipment_type}` | token | By equipment type |
| GET | `/api/models` | token | List |
| POST | `/api/models` | writer | Register |
| GET | `/api/models/{model_id}` | token | Read |
| DELETE | `/api/models/{model_id}` | writer | Delete |
| POST | `/api/models/{model_id}/deploy` | writer | Load-and-score gate |
| GET | `/api/models/{model_id}/download` | token | Artifact |
| GET | `/api/models/latest/{model_type}` | token | Latest by type |
| POST | `/api/models/compare` | token | Compare |
| POST | `/api/models/upload-capability` | writer | Demand-mint an upload capability handle ([model upload](model-upload.md)) |
| GET | `/api/registered-models`, `/api/registered-models/{name}` | token | Tenant-scoped MLflow registry read |
| PATCH | `/api/registered-models/{name}` | writer | Edit the description |
| POST | `/api/registered-models/{name}/versions/{version}/promote` | writer | Make a notebook-registered version a platform model ([promotion](#promotion)) |
| GET | `/api/experiments`, `/api/experiments/{name}/runs` | token | Tenant-scoped experiment read |
| GET | `/api/ml/capabilities` | none | Servable flavors and detectors, discovered from the registry |
| POST | `/api/inference/predict` | token or internal | Score a reading |
| POST | `/api/drift/evaluate` | token or internal | Data and prediction drift for a model |

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
| `autoencoder` | reconstruction error | `mlflow.sklearn` | Reconstruction distance |

Every detector runs in-process. Frameworks the serving image does not carry (torch, GPU) are refused at
the gate; serving them in a separate runtime from an ONNX export is accepted but not built
([ADR-0036](../../ADR/ADR-0036-out-of-process-model-runtime.md)).

## Model record (`ml_models`)

| Column | Meaning |
|---|---|
| `feature_config_id` | Feature config for inference |
| `reference_profile` (JSONB) | Drift baseline: the feature sample, the derived scores, and the baseline's state ([drift](#drift-post-apidriftevaluate)) |
| `sensor_ids` | The equipment's sensors of the feature config's base sensors, resolved at registration; drift reads its window from them |
| `compatibility_status`, `compatibility_detail`, `compatibility_checked_at` | Training vs serving environment verdict, set at registration and deployment; null = never evaluated |
| `provenance`, `artifact_uri` | Kernel-trained (by run) or uploaded (by admitted location) |
| `detector` | Detector for serving |

## Inference (`POST /api/inference/predict`)

| Step | Detail |
|---|---|
| Tenant | From the verified identity, or the `company_id` body field on an internal-token call |
| Load | Model record and feature config; model from the warm cache or a cold load off the event loop |
| Address check | A run must belong to one of the tenant's experiments; an artifact must be an admitted upload under the tenant's prefix; re-checked on every call |
| Features | Feature config applied; stateful slots neutralised when the kill-switch is off |
| Score | Detector named by the record; response `degraded: true` when any feature was neutralised |

Kernel-trained tree-based sklearn models need the skops grant at log time:
`mlflow.sklearn.log_model(model, name="model", skops_trusted_types=industryflow.SKOPS_TRUSTED_TYPES)`.

A served model, kernel-authored or uploaded, loads through `mlflow.sklearn` (skops) or `mlflow.xgboost`
(native booster) only; a pickle, a custom `mlflow.pyfunc` model or shipped code is refused at
registration (422), deployment (409) and every load
([ADR-0037](../../ADR/ADR-0037-no-author-code-on-the-serving-side.md)).

## Drift (`POST /api/drift/evaluate`)

| Item | Detail |
|---|---|
| Feature sample | Supplied at registration: `reference_profile` in `POST /api/models` (the upload wizard's sample file), or the run's `industryflow/feature_sample.json` at promotion; format and row bound (`DEFAULT_MAX_SAMPLE_ROWS`, a longer sample evenly down-sampled on every path, [ADR-0035](../../ADR/ADR-0035-drift-baseline-derived-scores.md) dec 8) `drift/reference_profile.py` |
| Accepted when | Its columns are exactly the bound feature config's base sensors and it carries no scores; otherwise the model registers without a baseline and `baseline.reason` says why ([ADR-0035](../../ADR/ADR-0035-drift-baseline-derived-scores.md)) |
| Prediction scores | Derived at every deployment to `production`/`active`: each complete sample row through the feature config and the model's detector, in the probe's load for an upload and serving's load otherwise |
| Detector cannot score the sample | Deployment proceeds; data drift only, with the reason |
| Current window | Trailing window of the model's `sensor_ids`, aligned to instants where every compared sensor reported |
| Engine | `evidently` |
| Output | Data drift (drifted-feature share), prediction drift, `prediction_drift_reason` when prediction drift cannot be computed, `baseline` |
| Caller | `alert-service-detector` `statistical` rules ([alerting](alerting.md)) |

`baseline` on `GET /api/models`, `GET /api/models/{id}`, registration and promotion:

| Field | Meaning |
|---|---|
| `data_drift` | `false`: no accepted sample, not monitored |
| `prediction` | `derived` (scores from the last deployment), `supplied` (scores from before ADR-0035, replaced at the next deployment) or `null` |
| `reason` | Why monitoring is less than data and prediction drift |

## Promotion

`POST /api/registered-models/{name}/versions/{version}/promote` registers a notebook-registered version as
a platform model ([ADR-0040](../../ADR/ADR-0040-notebook-model-promotion.md)). The Models page offers it per
version.

| Body field | Default | Meaning |
|---|---|---|
| `feature_config_id` | required | The model's inputs |
| `equipment_id` | none | Equipment whose sensors drift reads; every base sensor must exist on it |
| `detector` | `sklearn` | What the output means ([ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md)) |
| `model_type` | `anomaly_detection` | Free-text type |

| Taken from the registry | Into |
|---|---|
| Version's run | `mlflow_run_id` (provenance `kernel`), `mlflow_experiment_id`, run metrics as `training_metrics` |
| Version or model description | `description` |
| Run artifact `industryflow/feature_sample.json`, at most `MAX_SAMPLE_BYTES` (`drift/reference_profile.py`) | `reference_profile` |

| Response | Cause |
|---|---|
| 201 | Registered; `baseline` states what drift monitoring it gets |
| 404 | No such version in the caller's tenant, or its run is not one of the tenant's |
| 409 | That name and version is already a platform model |
| 422 | The registration gate refused it, an unknown feature config, or a base sensor missing on the equipment |

Model training is manual, in notebooks ([notebooks](notebooks.md)); no training service exists.
