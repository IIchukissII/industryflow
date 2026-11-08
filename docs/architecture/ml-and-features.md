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
| Window baselines read from the Spark aggregates; neutral value on a missing baseline | [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md), [ADR-0044](../../ADR/ADR-0044-complete-model-input.md) dec 3 |
| A reading lacking an input, or a definition that cannot be computed, is refused; no substitute values | [ADR-0044](../../ADR/ADR-0044-complete-model-input.md) |
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

| Built-in transform | Stateful | Definition names |
|---|---|---|
| `identity` | no | `sensor` |
| `polynomial` | no | `sensor`, numeric `params.power` |
| `interaction` | no | `sensors` (2), `params.operation`: `ratio`, `difference`, `product` or `sum` |
| `deviation` | no | `sensor`, numeric `params.baseline` |
| `statistical` | yes | `sensor`, `params.stat_type`, `params.granularity`: one of the windows in `industryflow_contracts.windows` |
| `rolling_stat` | yes; not implemented, a feature using it is refused | — |

A definition missing a named parameter, naming one of the wrong kind or a non-finite number, or whose
`params` is not an object, is refused with `422`; no default stands in for it
([ADR-0044](../../ADR/ADR-0044-complete-model-input.md) dec 2).

A stateful feature reads the baseline of the reading's equipment in the window `params.granularity`
names; a reading naming no valid `equipment_id` is refused with `422`. A ratio whose denominator is
`0`, or any computed value that is not a finite number, fails the score with `500`
([ADR-0044](../../ADR/ADR-0044-complete-model-input.md) dec 1, 4).

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
| Input | A reading lacking a sensor the config reads (absent or not a finite number) is `422` naming the sensors |
| Features | Feature config applied; a definition its transform cannot interpret is `422`; any other feature failure is `500`; stateful slots neutralised when the kill-switch is off or their aggregate is unavailable |
| Score | Detector named by the record; response `degraded: true` and `neutralized_features` when any slot was neutralised |

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
| Run artifact `industryflow/feature_sample.json`, at most `MAX_SAMPLE_BYTES` (`drift/reference_profile.py`); a run that logged none gives no baseline, which `baseline` states | `reference_profile` |

| Response | Cause |
|---|---|
| 201 | Registered; `baseline` states what drift monitoring it gets |
| 404 | No such version in the caller's tenant, or its run is not one of the tenant's |
| 409 | That name and version is already a platform model |
| 422 | The registration gate refused it, an unknown feature config, or a base sensor missing on the equipment |
| 502 | The tracking server could not be read (registry, run, artifact listing or sample) |

Model training is manual, in notebooks ([notebooks](notebooks.md)); no training service exists.

## ADR-0021 dec 1-3, 5

The alert worker schedules a drift evaluation per statistical rule; the ML service computes data and prediction drift.

| Code | Role |
|---|---|
| `services/ml_service/api/drift` | Drift computation |
| `services/alert_service/worker/rules_engine.py` | Statistical rule evaluation |

## ADR-0023 dec 2

The baseline is read from the materialized per-tenant aggregate table.

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/baseline_provider.py` | Baseline read |

## ADR-0023 dec 7

| Store | Holds | Bound |
|---|---|---|
| ML service baseline cache | Returned aggregate means, keyed by tenant schema, equipment, sensor and window | TTL `BASELINE_CACHE_TTL` (5 s), `BASELINE_CACHE_MAX_ENTRIES` (4096), least recently used evicted |
| Alert worker latest-value store (Redis) | Recent readings per tenant, equipment and sensor; ML rules read the latest of each as one snapshot | `FEATURE_MAX_AGE_SECONDS` (300 s) per reading |
| ML service capability store (Redis) | Upload-capability bindings only; no feature data | — |

The `statistical` transform reads its window mean and deviation from the aggregate; no windowing runs on the serving side.

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/baseline_provider.py::AggregateBaselineProvider` | Aggregate read behind the bounded cache |
| `services/ml_service/api/extensions/builtins.py::statistical` | Window statistics from the aggregate |
| `services/alert_service/worker/feature_store.py::FeatureStore` | Latest-value snapshot store |
| `services/ml_service/api/config.py::CAPABILITY_REDIS_URL` | Capability store, not a feature store |
| `services/ml_service/tests/test_statistical_baseline.py` | Proof: aggregate-backed baseline and cache bound |

## ADR-0023 dec 9

Offline training derives the window through the shared window contract.

| Code | Role |
|---|---|
| `libs/industryflow_contracts` | Window contract |
| `services/ml_service/tests/test_train_serve_agreement.py` | Train/serve agreement |

## ADR-0027 dec 1-4

The compatibility gate reads the artifact's flavor and pinned requirements from its metadata, without loading it, and compares them with the installed distributions.

| Check | Refuses when |
|---|---|
| Flavor | The flavor is neither an in-process loader nor claimed by a loaded detector, or its framework is not installed |
| Flavor framework and serialisers | Declared but not installed |
| numpy | Major differs |
| scikit-learn | Major.minor differs |
| skops, xgboost | Major differs, or serving is older than the declared version |
| pandas | Never at the gate; bound at build time by the parity script |
| Any other declared package | Never; recorded only |

The verdict is `compatible`, `patch_drift` or `incompatible`; a refusal carries its reasons and the per-library faults. The supported flavor set is the in-process loaders plus the flavors loaded detectors claim; an upload's flavor is matched against the detector registry.

| Code | Role |
|---|---|
| `services/ml_service/api/model_compat.py::evaluate` | Verdict against the installed environment |
| `services/ml_service/api/model_compat.py::SERIALIZATION_CRITICAL` | Version rules per library |
| `services/ml_service/api/model_compat.py::_supported_flavor` | Supported flavor set: loaders plus detector claims |
| `services/ml_service/api/uploaded_model.py::judge_semantics` | Upload flavor matched against the detector registry |
| `services/frontend/src/pages/ModelUploadWizard.jsx::refusalText` | Refusal reason shown to the uploader |
| `services/ml_service/tests/test_model_compat.py` | Proof: version rules, unsupported framework refused |
| `services/ml_service/tests/test_uploaded_model.py::test_the_rule_asks_the_registry_and_keeps_no_list_of_its_own` | Proof: an installed adapter widens the set without code change |

## ADR-0027 dec 5

A parity script compares declared constraints with the image; a round-trip workflow logs, registers and scores a model of each flavor.

| Code | Role |
|---|---|
| `scripts/check_model_env_parity.py` | Declared constraints against the image |
| `.github/workflows/model-artifact-roundtrip.yml` | Empirical round trip |

## ADR-0027 dec 6, 7, 13

Registration and deployment judge compatibility; the serving side grants deserialisation trust by provenance.

| Code | Role |
|---|---|
| `services/ml_service/api/model_compat.py` | Compatibility verdict |
| `services/ml_service/api/skops_trust.py` | Trusted types by provenance |

## ADR-0027 dec 8

The verdict is stored on the model record and shown on the models page as a badge and a parity ledger; the alert service and the retrain recommendation never read it.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/18-model-compatibility-status.sql` | Verdict, detail and check time on `ml_models` |
| `services/frontend/src/pages/MLModels.jsx::ParityLedger` | Verdict shown on the model |

## ADR-0027 dec 9

The parity script reads the serving pins from the one requirements file the serving Dockerfile copies; no other manifest exists.

| Code | Role |
|---|---|
| `scripts/check_model_env_parity.py::serving_requirements_path` | Serving pins from the image's own COPY |
| `services/ml_service/api/requirements.txt` | The serving image's manifest |
| `scripts/tests/test_model_env_parity.py::test_the_phantom_is_gone_and_stays_gone` | Proof: no uninstalled manifest |

## ADR-0027 dec 10

| Model state | Behaviour |
|---|---|
| Registered before the gate, no verdict | `compatibility_status` is null; already deployed, it stays served; the models page marks it `Not evaluated` |
| Deployed again | The deployment gate evaluates it, records the verdict and refuses it with 409 when `incompatible` |

Every load re-checks the tenant address only, not compatibility.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/18-model-compatibility-status.sql` | Nullable verdict |
| `services/ml_service/api/routers/models.py::deploy_model` | Verdict at deployment |
| `services/ml_service/api/model_loader.py::check_address` | Load-time check, without a compatibility verdict |
| `services/frontend/src/pages/MLModels.jsx` | `Not evaluated` mark on a platform model with no verdict |
| `services/frontend/src/pages/MLModels.test.jsx` | Proof: a model never evaluated is marked |

## ADR-0027 dec 12

The parity script compares the authoring kernel with the serving image; the analytics kernel image is not read.

| Code | Role |
|---|---|
| `scripts/check_model_env_parity.py::KERNEL_DOCKERFILE` | Authoring kernel as the training end |

## ADR-0028 dec 1-3

Detectors register with the score semantics they implement (see Detectors). A detector that cannot establish its model's semantics raises `UninterpretableModel`; inference answers 422 and scores nothing. A model record with no detector is refused at scoring.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions` | Semantics vocabulary and built-in detectors |
| `services/ml_service/api/extensions/__init__.py::UninterpretableModel` | Refusal to score |
| `services/ml_service/api/routers/inference.py` | Dispatch on the recorded detector; refusal as 422 |
| `services/ml_service/api/uploaded_model.py::judge_semantics` | Upload without declared semantics refused |
| `infrastructure/timescaledb/init-scripts/20-model-detector.sql` | Detector recorded on the model |
| `services/ml_service/tests/test_detectors.py::TestAFloatIsNotADeclaration` | Proof: a float output is not read as a score |

## ADR-0028 dec 4

| Path | Flavor check |
|---|---|
| Kernel registration | The named detector, `sklearn` by default, must list the artifact's flavor; a detector listing none scores no flavor |
| Upload registration | A detector must implement the declared semantics and list the flavor |
| Every registration and deployment | The compatibility gate refuses a flavor outside the supported set |

| Code | Role |
|---|---|
| `services/ml_service/api/routers/models.py::_kernel_detector_or_refuse` | Kernel detector held to its flavors |
| `services/ml_service/tests/test_registration_gate_uploaded.py::test_a_kernel_model_naming_a_detector_that_claims_no_flavor_is_refused` | Proof: an empty flavor list claims nothing on the kernel path |
| `services/ml_service/api/extensions/__init__.py::detectors_for` | Detectors by semantics and flavor |
| `services/ml_service/api/model_compat.py::_supported_flavor` | Flavor set at the gate |

## ADR-0028 dec 5

The supported set is read from the registry and served.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/capabilities.py` | Served supported set |

## ADR-0028 dec 7

No out-of-process runtime is built. A flavor whose framework the serving image does not install is refused at the compatibility gate and listed under `unservable_flavors` in `GET /api/ml/capabilities`.

| Code | Role |
|---|---|
| `services/ml_service/api/model_compat.py::FLAVOR_LIBRARY` | Framework each flavor needs |
| `services/ml_service/api/routers/capabilities.py::get_capabilities` | Unservable flavors named |
| `services/ml_service/tests/test_model_compat.py::test_a_torch_model_is_refused_honestly_not_served_badly` | Proof: a torch artifact is refused |

## ADR-0035 dec 1, 4

The sample is stored as a reference profile (see Drift).

| Code | Role |
|---|---|
| `services/ml_service/api/drift/reference_profile.py` | Profile format |

## ADR-0035 dec 2, 3

The deployment gate scores the sample through the declared detector.

| Code | Role |
|---|---|
| `services/ml_service/api/load_probe.py` | Isolated load-and-score |

## ADR-0035 dec 5

| Sample outcome at the gate | Stored profile |
|---|---|
| Scored | `prediction_baseline.origin` = `derived`, with the time |
| Detector refuses, scoring fails or stalls, no complete row, no feature configuration | `prediction_baseline.origin` = null, with the reason; data drift still runs |

The load verdict is read before the sample's outcome, so a sample never fails a deployment. The detector context carries the model's `reconstruction_scale`. Drift evaluation returns `prediction_drift_reason` when there is no prediction baseline.

| Code | Role |
|---|---|
| `services/ml_service/api/baseline_gate.py::Plan` | Scores or the reason there are none |
| `services/ml_service/api/drift/reference_profile.py::without_prediction_baseline` | Data-drift-only profile |
| `services/ml_service/api/load_probe.py::_result` | Load verdict before the sample's outcome |
| `services/ml_service/tests/test_load_probe.py::test_a_sample_that_stalls_after_a_good_load_is_not_a_refusal` | Proof: a stalled sample is not a refusal |
| `services/ml_service/tests/test_model_admission.py::test_a_detector_that_cannot_score_the_sample_leaves_data_drift_only` | Proof: data drift only |

## ADR-0035 dec 6

Direct registration, upload and promotion all pass the sample through the same acceptance step. The upload wizard sends the chosen sample file as `reference_profile` with the feature configuration.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/models.py::_bind_inputs_and_baseline` | One acceptance step for every path |
| `services/frontend/src/pages/ModelUploadWizard.jsx` | Sample sent with an upload |

## ADR-0035 dec 7

A profile with a `prediction_column` and no `prediction_baseline` holds supplied scores. Its state reads `supplied`; the models page shows it. The next deployment replaces the supplied scores with derived ones.

| Code | Role |
|---|---|
| `services/ml_service/api/drift/reference_profile.py::baseline_state` | `supplied` state |
| `services/frontend/src/pages/MLModels.jsx::baselineText` | State shown on the model |
| `services/ml_service/tests/test_drift_reference_profile.py::test_the_baseline_says_what_monitoring_can_do` | Proof: a legacy profile reads `supplied` |

## ADR-0035 dec 8

| Bound | Value | Where |
|---|---|---|
| Rows stored and scored | `DEFAULT_MAX_SAMPLE_ROWS` (5000), evenly down-sampled | Acceptance on every path; again at the deployment gate |
| Sample read from a run | `MAX_SAMPLE_BYTES` (16 MiB); a larger file is not read | Promotion |
| Registration request body | 16 MiB | Edge proxy, `/api/models` |

| Code | Role |
|---|---|
| `services/ml_service/api/drift/reference_profile.py::accepted_sample` | Row bound and down-sample |
| `services/ml_service/api/baseline_gate.py::plan` | Bound applied before scoring |
| `services/ml_service/api/routers/registered_models.py::_read_sample` | Size-bounded read from a run |
| `services/frontend/nginx.conf.template` | Body size at the edge |
| `services/ml_service/tests/test_drift_reference_profile.py::test_an_accepted_sample_is_bounded_in_rows_on_every_path` | Proof: row bound on every path |
| `services/ml_service/tests/test_model_admission.py::test_a_sample_stored_before_the_bound_is_scored_and_stored_at_it` | Proof: gate scores at the bound |

## ADR-0036 dec 7

The deployment gate loads and scores an upload in the live runtime.

| Code | Role |
|---|---|
| `services/ml_service/api/load_probe.py` | Isolated load-and-score |

## ADR-0037 dec 2-4, 6

Every serving-side load is data-only; trust is granted by provenance; a model that fails is refused at load.

| Code | Role |
|---|---|
| `services/ml_service/api/skops_trust.py` | Trusted types by provenance |
| `services/ml_service/api/routers/models.py` | Registration and deployment checks |

## ADR-0037 dec 5

Built-in detectors claim only flavors a data-only loader serves.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/builtins_detectors.py` | Built-in detectors |

## ADR-0040 dec 2-4

Promotion reads the sample from the run and registers through the same gate (see Promotion). The promotion request carries the feature configuration, equipment and detector the user chose; the run supplies only its metrics, description and sample. The form offers the loaded detectors, `sklearn` by default.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/registered_models.py::PromoteRequest` | The user's choices |
| `services/ml_service/api/routers/registered_models.py::promote_model_version` | Registration from the run |
| `services/ml_service/tests/test_promotion_integration.py` | Promotion against the live tracking server |

## ADR-0041 dec 1-4

The database refuses a change to, or deletion of, a bound configuration (see Feature configuration). A trigger on `ml_models` refuses any change of a model's `feature_config_id` and takes a share lock on the configuration it binds.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/30-bound-feature-config.sql` | Binding rule |
| `infrastructure/timescaledb/init-scripts/30-bound-feature-config.sql::hold_feature_config_binding` | Binding fixed at registration |
| `infrastructure/timescaledb/tests/test_bound_feature_config.py::test_a_binding_is_fixed` | Proof: a binding cannot change |

## ADR-0041 dec 3

The API reports the refusal.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/feature_configs.py` | Refusal reported |

## ADR-0044 dec 1-5

The engine refuses a reading with a missing sensor or an uninterpretable definition and fails the score on any other feature failure. No partial-scoring path exists; the equipment table has no partial-batch columns.

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/engine.py` | Refusals and substitutes |
| `services/ml_service/api/extensions/builtins.py` | Built-in transforms |
| `infrastructure/timescaledb/init-scripts/33-equipment-batch-settings-removed.sql` | Partial-batch columns dropped from every tenant |
| `services/alert_service/worker/rules_engine.py` | Dec 3: an ML alert records its degraded score and the neutralized features |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_builtin_definition_missing_its_parameter_is_a_422` | Proof: dec 2 for every built-in parameter |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_feature_that_fails_to_compute_fails_the_score` | Proof: dec 4 |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_stateful_feature_for_a_reading_naming_no_equipment_is_a_422` | Proof: dec 1 for the equipment |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_feature_that_is_not_a_finite_number_fails_the_score` | Proof: dec 4 for non-finite values |
