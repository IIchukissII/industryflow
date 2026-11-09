<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ML and feature engineering

The manual of [ADR-0021](../../ADR/ADR-0021-model-drift-monitoring-the-statistical-alert-lane.md), [ADR-0023](../../ADR/ADR-0023-stream-materialized-feature-engineering.md), [ADR-0041](../../ADR/ADR-0041-a-model-s-feature-configuration-is-fixed-once-bound.md), [ADR-0044](../../ADR/ADR-0044-a-model-is-scored-on-complete-computed-input-or-not-at-all.md), [ADR-0028](../../ADR/ADR-0028-declared-score-semantics-and-the-model-adapter-contract.md), [ADR-0027](../../ADR/ADR-0027-the-model-artifact-declares-the-serving-environment-satisfies-or-refuses.md), [ADR-0037](../../ADR/ADR-0037-no-model-load-on-the-serving-side-runs-author-code-whatever-the-provenance.md), [ADR-0036](../../ADR/ADR-0036-out-of-process-model-runtime-for-frameworks-the-serving-image-does-not-carry.md), [ADR-0035](../../ADR/ADR-0035-drift-baseline.md), [ADR-0040](../../ADR/ADR-0040-a-notebook-registered-model-version-becomes-a-platform-model-by-promotion.md): feature configurations, the model registry, inference, detectors, drift and promotion in `ml-service-api` (port 8002).

## ADR-0021 dec 1-3, 5

The alert worker schedules a drift evaluation per statistical rule ([alerting](alerting.md#adr-0021-dec-2-3-5)); the ML service computes data and prediction drift.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/drift.py::evaluate_drift` | Drift endpoint: window read, current predictions |
| `services/ml_service/api/drift/score.py::compute_drift` | Drift computation |
| `services/alert_service/worker/rules_engine.py` | Statistical rule evaluation |

### Drift (`POST /api/drift/evaluate`)

| Item | Detail |
|---|---|
| Feature sample | Supplied at registration: `reference_profile` in `POST /api/models` (the upload wizard's sample file), or the run's `industryflow/feature_sample.json` at promotion; format and row bound (`DEFAULT_MAX_SAMPLE_ROWS`, a longer sample evenly down-sampled on every path, [ADR-0035](../../ADR/ADR-0035-drift-baseline.md) dec 8) `drift/reference_profile.py` |
| Accepted when | Its columns are exactly the bound feature config's base sensors and it carries no scores; otherwise the model registers without a baseline and `baseline.reason` says why ([ADR-0035](../../ADR/ADR-0035-drift-baseline.md)) |
| Prediction scores | Derived at every deployment to `production`/`active`: each complete sample row through the feature config and the model's detector, in the probe's load for an upload and serving's load otherwise. A config with a stateful feature derives none: a sample row carries no time to read its window as of |
| Detector cannot score the sample | Deployment proceeds; data drift only, with the reason |
| Current window | Trailing window of the model's `sensor_ids`, aligned to instants where every compared sensor reported a finite value |
| Current predictions | Up to `MAX_PREDICTION_ROWS` (500) instants, each through the feature config with stateful features read as of that instant; a slot that would take a substitute (kill-switch off, no window yet, no baseline source) leaves prediction drift not computed, with the reason |
| Engine | `evidently` |
| Output | Data drift (drifted-feature share), prediction drift, `prediction_drift_reason` when prediction drift cannot be computed, `baseline` |
| Caller | `alert-service-detector` `statistical` rules ([alerting](alerting.md)) |

`baseline` on `GET /api/models`, `GET /api/models/{id}`, registration and promotion:

| Field | Meaning |
|---|---|
| `data_drift` | `false`: no accepted sample, not monitored |
| `prediction` | `derived` (scores from the last deployment), `supplied` (scores from before ADR-0035, replaced at the next deployment) or `null` |
| `reason` | Why monitoring is less than data and prediction drift |

## ADR-0021 dec 6

One evaluation reads one model of one tenant. The tenant comes from the verified identity, or from the `company_id` body field on an internal-token call; the model record and the trailing window are read on that tenant's schema.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/inference.py::_resolve_company_id` | Tenant from the token, or from the body on an internal call |
| `services/ml_service/api/repository.py::get_model_by_id` | Model and profile read on the tenant connection |
| `services/ml_service/api/repository.py::read_sensor_window` | Window read on the tenant connection |

### Endpoint auth

Auth "token" is an access token (`if_access` cookie or bearer); "writer" is a token whose account
has role `engineer` or `admin`, or is a superuser, else **403**
([roles](../operations/user-management.md#roles)); "internal" is the internal service token
([authentication](../operations/authentication.md)).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/drift/evaluate` | token or internal | Data and prediction drift for a model |

## ADR-0023 dec 1

The window statistics a stateful feature reads (mean, minimum, maximum, standard deviation, count) are computed by the `spark-aggregations` streaming job in Spark's state store: one watermarked windowed group-by per granularity of `industryflow_contracts.windows`, keyed by sensor, equipment and tenant, emitted once when the window closes.

| Code | Role |
|---|---|
| `services/spark_jobs/kafka_aggregations.py::aggregate_readings` | Windowed aggregation in the state store |
| `services/spark_jobs/kafka_aggregations.py::create_aggregation_stream` | One query per granularity, append mode, checkpointed |
| `services/spark_jobs/aggregation_windows.py` | Window widths and lateness from the shared contract |

## ADR-0023 dec 2

The baseline is read from the materialized per-tenant aggregate table, `sensor_aggregations_<granularity>`.

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/baseline_provider.py` | Baseline read |
| `services/spark_jobs/kafka_aggregations.py::write_aggregations_to_db` | Idempotent upsert into each tenant's aggregate table |

## ADR-0023 dec 3

Stateless transforms are registered without `stateful` and compute per reading in the ML service's feature engine, reading no window. Stored per tenant in `feature_engineering_configs`: `base_sensors` and an ordered list of `transformations`, each with a `type` and params.

| Built-in transform | Stateful | Definition names |
|---|---|---|
| `identity` | no | `sensor` |
| `polynomial` | no | `sensor`, numeric `params.power` |
| `interaction` | no | `sensors` (2), `params.operation`: `ratio`, `difference`, `product` or `sum` |
| `deviation` | no | `sensor`, numeric `params.baseline` |
| `statistical` | yes | `sensor`, `params.stat_type`, `params.granularity`: one of the windows in `industryflow_contracts.windows` |
| `rolling_stat` | yes; not implemented, a feature using it is refused | — |

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/builtins.py` | Built-in transforms |
| `services/ml_service/api/extensions/__init__.py::register_transform` | Transform registry, `stateful` flag |
| `services/ml_service/api/feature_engineering/engine.py` | Feature engine |

## ADR-0023 dec 4-6

Not yet realised.

## ADR-0023 dec 7

| Store | Holds | Bound |
|---|---|---|
| ML service baseline cache | Returned aggregate means of live reads, keyed by tenant schema, equipment, sensor and window; a read as of a past instant is not cached | TTL `BASELINE_CACHE_TTL` (5 s), `BASELINE_CACHE_MAX_ENTRIES` (4096), least recently used evicted |
| Alert worker latest-value store (Redis) | Recent readings per tenant, equipment and sensor; ML rules read the latest of each as one snapshot | `FEATURE_MAX_AGE_SECONDS` (300 s) per reading |
| ML service capability store (Redis) | Upload-capability bindings only; no feature data | — |

The `statistical` transform reads its window mean and deviation from the aggregate; no windowing runs on the serving side.

| Provider answer | When | Result |
|---|---|---|
| `{"mean", "std"}` | The newest window, or with `as_of` the newest at or before the window written by that instant | Value |
| `None` | The sensor has no window yet, or the store is out (cannot be reached, out of resources, shutting down, no answer in `BASELINE_QUERY_TIMEOUT`) | Unavailable aggregate |
| `UnknownBaselineInput` | The equipment has no sensor of that name in the tenant | `422` |
| Any other error | A defect of the query or schema | The score fails |

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/baseline_provider.py::AggregateBaselineProvider` | Aggregate read behind the bounded cache |
| `services/ml_service/api/feature_engineering/baseline_provider.py::BASELINE_SELECT` | The read: newest window, unknown sensor told apart |
| `infrastructure/timescaledb/tests/test_baseline_provider_live.py` | Proof on a real tenant: newest, as of, no window, unknown sensor |
| `services/ml_service/api/extensions/builtins.py::statistical` | Window statistics from the aggregate |
| `services/alert_service/worker/feature_store.py::FeatureStore` | Latest-value snapshot store |
| `services/ml_service/api/config.py::CAPABILITY_REDIS_URL` | Capability store, not a feature store |
| `services/ml_service/tests/test_statistical_baseline.py` | Proof: aggregate-backed baseline and cache bound |

## ADR-0023 dec 8

No stream-computed feature, no dedicated feature table and no rolling statistic exist. `rolling_stat` is registered as a stateful transform whose check refuses every definition ("rolling statistics are not implemented").

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/builtins.py::rolling_stat` | Registered placeholder, refused |
| `services/ml_service/tests/test_complete_model_input.py` | Proof: a `rolling_stat` definition is refused |

## ADR-0023 dec 9

Offline training derives the window through the shared window contract; so does drift, scoring a stored reading. A row whose own reading is missing stays missing in training.

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/windows.py` | Window contract |
| `services/ml_service/api/feature_engineering/baseline_provider.py::AsOf` | The provider handed to transforms when a stored reading is scored |
| `services/ml_service/tests/test_train_serve_agreement.py` | Train/serve agreement |
| `services/ml_service/tests/test_drift_endpoint.py::test_each_row_reads_the_window_written_by_its_own_instant` | Proof: drift reads each row's window |

## ADR-0041 dec 1-4

The database refuses a change to, or deletion of, a bound configuration. A trigger on `ml_models` refuses any change of a model's `feature_config_id` and takes a share lock on the configuration it binds. `ml_models.feature_config_id` links a model to its config.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/30-bound-feature-config.sql` | Binding rule |
| `infrastructure/timescaledb/init-scripts/30-bound-feature-config.sql::hold_feature_config_binding` | Binding fixed at registration |
| `infrastructure/timescaledb/tests/test_bound_feature_config.py::test_a_binding_is_fixed` | Proof: a binding cannot change |

### Feature configuration endpoints

Auth as in [Endpoint auth](#endpoint-auth).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/feature-configs` | token | List |
| POST | `/api/feature-configs` | writer | Create |
| GET | `/api/feature-configs/{config_id}` | token | Read |
| PUT, DELETE | `/api/feature-configs/{config_id}` | writer | Replace, delete; a config a model binds keeps `base_sensors` and `transformations` and is not deleted: **409** naming the models |
| GET | `/api/feature-configs/equipment/{equipment_type}` | token | By equipment type |

## ADR-0041 dec 3

The API reports the refusal.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/feature_configs.py` | Refusal reported |

## ADR-0044 dec 1-5

The engine refuses a reading with a missing sensor, an equipment without a sensor a stateful feature reads, or an uninterpretable definition, and fails the score on any other feature failure. Drift and the deployment gate build the engine with `substitutes=False`: where a slot would take a substitute it raises `SubstituteRefused`, and prediction drift or its baseline is not computed, with the reason. No partial-scoring path exists; the equipment table has no partial-batch columns.

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/engine.py` | Refusals and substitutes |
| `services/ml_service/api/extensions/builtins.py` | Built-in transforms |
| `services/ml_service/api/feature_engineering/kill_switch.py` | Kill-switch (`public.platform_config.stateful_features_enabled`, [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) |
| `services/ml_service/api/routers/inference.py` | `POST /api/inference/predict` |
| `services/ml_service/api/model_cache.py` | Warm model cache (LRU, `MODEL_CACHE_SIZE`, default 8) |
| `infrastructure/timescaledb/init-scripts/33-equipment-batch-settings-removed.sql` | Partial-batch columns dropped from every tenant |
| `services/alert_service/worker/rules_engine.py` | Dec 3: an ML alert records its degraded score and the neutralized features |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_builtin_definition_missing_its_parameter_is_a_422` | Proof: dec 2 for every built-in parameter |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_feature_that_fails_to_compute_fails_the_score` | Proof: dec 4 |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_stateful_feature_for_a_reading_naming_no_equipment_is_a_422` | Proof: dec 1 for the equipment |
| `services/ml_service/tests/test_inference_dispatch.py::test_a_feature_that_is_not_a_finite_number_fails_the_score` | Proof: dec 4 for non-finite values |
| `services/ml_service/tests/test_stateful_kill_switch.py::test_an_unknown_sensor_is_refused_not_served_neutral` | Proof: dec 1 for an equipment without the sensor |
| `services/ml_service/tests/test_drift_endpoint.py::test_a_substitute_value_leaves_prediction_drift_not_computed` | Proof: dec 3, no substitute in drift |
| `services/ml_service/tests/test_drift_endpoint.py::test_the_deploy_gate_derives_no_baseline_for_stateful_features` | Proof: dec 3, none in a baseline |
| `services/ml_service/tests/test_statistical_baseline.py::test_a_read_error_that_is_not_an_outage_fails_the_score` | Proof: dec 4, only an outage is unavailable |

### Refusals

A definition missing a named parameter, naming one of the wrong kind or a non-finite number, or whose
`params` is not an object, is refused with `422`; no default stands in for it (dec 2).

A stateful feature reads the baseline of the reading's equipment in the window `params.granularity`
names; a reading naming no valid `equipment_id`, or an equipment without the feature's sensor, is
refused with `422`. A ratio whose denominator is
`0`, or any computed value that is not a finite number, fails the score with `500` (dec 1, 4).

### Inference (`POST /api/inference/predict`)

| Step | Detail |
|---|---|
| Tenant | From the verified identity, or the `company_id` body field on an internal-token call |
| Load | Model record and feature config; model from the warm cache or a cold load off the event loop |
| Address check | A run must belong to one of the tenant's experiments; an artifact must be an admitted upload under the tenant's prefix; re-checked on every call |
| Input | A reading lacking a sensor the config reads (absent or not a finite number) is `422` naming the sensors |
| Features | Feature config applied; a definition its transform cannot interpret is `422`; any other feature failure is `500`; stateful slots neutralised (neutral value, dec 3) when the kill-switch is off or their aggregate is unavailable |
| Score | Detector named by the record; response `degraded: true` and `neutralized_features` when any slot was neutralised |

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/inference/predict` | token or internal ([Endpoint auth](#endpoint-auth)) | Score a reading |

## ADR-0028 dec 1-3

Detectors register with the score semantics they implement. A detector that cannot establish its model's semantics raises `UninterpretableModel`; inference answers 422 and scores nothing. `ml_models.detector` names the detector; a model record with no detector is refused at scoring.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py` | Semantics vocabulary, detector registry |
| `services/ml_service/api/extensions/builtins_detectors.py` | Built-in detectors |
| `services/ml_service/api/extensions/__init__.py::UninterpretableModel` | Refusal to score |
| `services/ml_service/api/routers/inference.py` | Dispatch on the recorded detector; refusal as 422 |
| `services/ml_service/api/uploaded_model.py::judge_semantics` | Upload without declared semantics refused |
| `infrastructure/timescaledb/init-scripts/20-model-detector.sql` | Detector recorded on the model |
| `services/ml_service/tests/test_detectors.py::TestAFloatIsNotADeclaration` | Proof: a float output is not read as a score |

### Detectors

| Detector | Semantics | Flavors | Score from |
|---|---|---|---|
| `sklearn` | anomaly probability (compatibility name) | `mlflow.sklearn`, `mlflow.xgboost` | Estimator capability, `predict_proba` first |
| `direct` | direct 0–1 score | `mlflow.sklearn`, `mlflow.xgboost` | Model output; outside 0–1 is refused |
| `outlier` | outlier score | `mlflow.sklearn` | `decision_function` |
| `autoencoder` | reconstruction error | `mlflow.sklearn` | Reconstruction distance |

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

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/ml/capabilities` | none | Servable flavors and detectors, discovered from the registry |

## ADR-0028 dec 6

Every detector runs in-process. The `autoencoder` detector scores a scikit-learn estimator that reproduces its input, loaded through `mlflow.sklearn`: the score is `mse / (mse + scale)`, the mean squared reconstruction error against the model's `reconstruction_scale`. An output whose shape differs from the input's, or a model with no scale, raises `UninterpretableModel`.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/builtins_detectors.py::autoencoder_detector` | In-process reconstruction-error detector |
| `services/ml_service/tests/test_detectors.py::TestAutoencoder` | Proof: an outlier reconstructs worse; no scale is a refusal |

## ADR-0028 dec 7

No out-of-process runtime is built. A flavor whose framework the serving image does not install is refused at the compatibility gate and listed under `unservable_flavors` in `GET /api/ml/capabilities`.

| Code | Role |
|---|---|
| `services/ml_service/api/model_compat.py::FLAVOR_LIBRARY` | Framework each flavor needs |
| `services/ml_service/api/routers/capabilities.py::get_capabilities` | Unservable flavors named |
| `services/ml_service/tests/test_model_compat.py::test_a_torch_model_is_refused_honestly_not_served_badly` | Proof: a torch artifact is refused |

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
| `services/ml_service/api/routers/models.py` | Model registry endpoints |

### Model record (`ml_models`)

| Column | Meaning |
|---|---|
| `feature_config_id` | Feature config for inference |
| `reference_profile` (JSONB) | Drift baseline: the feature sample, the derived scores, and the baseline's state ([drift](#drift-post-apidriftevaluate)) |
| `sensor_ids` | The equipment's sensors of the feature config's base sensors, resolved at registration; drift reads its window from them |
| `compatibility_status`, `compatibility_detail`, `compatibility_checked_at` | Training vs serving environment verdict, set at registration and deployment; null = never evaluated |
| `provenance`, `artifact_uri` | Kernel-trained (by run) or uploaded (by admitted location) |
| `detector` | Detector for serving |

### Model endpoints

Auth as in [Endpoint auth](#endpoint-auth).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/models` | token | List |
| POST | `/api/models` | writer | Register |
| GET | `/api/models/{model_id}` | token | Read |
| DELETE | `/api/models/{model_id}` | writer | Delete |
| POST | `/api/models/{model_id}/deploy` | writer | Load-and-score gate |
| GET | `/api/models/{model_id}/download` | token | Artifact |
| GET | `/api/models/latest/{model_type}` | token | Latest by type |
| POST | `/api/models/compare` | token | Compare |
| POST | `/api/models/upload-capability` | writer | Demand-mint an upload capability handle ([model upload](model-upload.md)) |

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

## ADR-0027 dec 11

The compatibility contract binds the model artifact only. Package installs in a running notebook environment and other base-image contents are outside it ([ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md)). A promoted notebook version meets the same gate as any registration ([ADR-0040 dec 2](#adr-0040-dec-2-4)).

## ADR-0027 dec 12

The parity script compares the authoring kernel with the serving image; the analytics kernel image is not read.

| Code | Role |
|---|---|
| `scripts/check_model_env_parity.py::KERNEL_DOCKERFILE` | Authoring kernel as the training end |

## ADR-0037 dec 1

No serving-side load runs author code. Every load downloads the artifact once, holds its manifest to `refuse_author_code` and deserialises it with a platform loader: `mlflow.sklearn` through skops, or `mlflow.xgboost` as a native booster. Shipped code, a loader outside `PLATFORM_LOADERS`, a sklearn model not serialised with skops, and an xgboost model class the platform does not list are refused.

| Code | Role |
|---|---|
| `services/ml_service/api/skops_trust.py::load_pyfunc` | The one load path, for serving and the deployment probe |
| `services/ml_service/api/skops_trust.py::refuse_author_code` | Manifest check before every load |
| `services/ml_service/api/skops_trust.py::PLATFORM_LOADERS` | The data-only loaders |
| `services/ml_service/tests/test_upload_author_code.py` | Proof: no load unpickles or imports what the artifact names |

## ADR-0037 dec 2-4, 6

Every serving-side load is data-only; trust is granted by provenance; a model that fails is refused at load.

A served model, kernel-authored or uploaded, loads through `mlflow.sklearn` (skops) or `mlflow.xgboost`
(native booster) only; a pickle, a custom `mlflow.pyfunc` model or shipped code is refused at
registration (422), deployment (409) and every load.

Kernel-trained tree-based sklearn models need the skops grant at log time:
`mlflow.sklearn.log_model(model, name="model", skops_trusted_types=industryflow.SKOPS_TRUSTED_TYPES)`.

| Code | Role |
|---|---|
| `services/ml_service/api/skops_trust.py` | Trusted types by provenance |
| `services/ml_service/api/model_loader.py` | Address check, then load |
| `services/ml_service/api/model_uri.py` | Model address |
| `services/ml_service/api/routers/models.py` | Registration and deployment checks |

## ADR-0037 dec 5

Built-in detectors claim only flavors a data-only loader serves; registering a detector that claims another flavor is refused.

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/builtins_detectors.py` | Built-in detectors |
| `services/ml_service/api/extensions/__init__.py::register_detector` | Claim outside `PLATFORM_LOADERS` refused |

## ADR-0037 dec 7

A kernel writes its run artifacts through the tracking gateway's artifact route, which judges no content; the structural admission runs only on the upload plane's commit. The serving side judges an artifact when it is registered, deployed or loaded.

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/gateway.py` | Artifact route (pre-signed, unjudged) and upload commit (admitted) |
| `services/notebook_tracking_gateway/admission.py` | Structural admission of uploads only |

## ADR-0036 dec 1

No separate model runtime is deployed: no runtime service exists in the compose stack or the chart. A flavor whose framework the serving image does not carry (torch, GPU) is refused at the compatibility gate with a reason; nothing falls back. See [ADR-0028 dec 7](#adr-0028-dec-7).

| Code | Role |
|---|---|
| `services/ml_service/api/model_compat.py::FLAVOR_LIBRARY` | Framework each flavor needs |
| `services/ml_service/api/model_compat.py::evaluate` | Refusal with its reason |

## ADR-0036 dec 2-6, 8, 9

Not yet realised.

## ADR-0036 dec 7

No model runtime is deployed, so no model for a runtime passes the gate: a framework the serving image does not carry is refused ([ADR-0036 dec 1](#adr-0036-dec-1)). The load-and-score probe in the serving image is ADR-0030's ([model upload](model-upload.md#adr-0030-dec-5-8)).

## ADR-0035 dec 1, 4

The sample is stored as a reference profile (see [drift](#drift-post-apidriftevaluate)).

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
| Detector refuses, scoring fails or stalls, no complete row, no feature configuration, a stateful feature, a substitute | `prediction_baseline.origin` = null, with the reason; data drift still runs |

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

## ADR-0040 dec 1

Promotion is a writer-only route of the ML service, called on the user's access token; the Models page offers **Promote** per version to a user who may write. Model training is manual, in notebooks ([notebooks](notebooks.md)); no training service exists.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/registered_models.py::promote_model_version` | Promotion behind `auth.require_writer` |
| `services/ml_service/api/auth.py::require_writer` | Writer role read from the account, else 403 |
| `services/frontend/src/pages/MLModels.jsx` | Promote action, shown to writers |
| `services/ml_service/tests/test_model_promotion.py::test_an_observer_cannot_promote` | Proof: an observer is refused |

## ADR-0040 dec 2-4

Promotion reads the sample from the run and registers through the same gate. The promotion request carries the feature configuration, equipment and detector the user chose; the run supplies only its metrics, description and sample. The form offers the loaded detectors, `sklearn` by default.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/registered_models.py::PromoteRequest` | The user's choices |
| `services/ml_service/api/routers/registered_models.py::promote_model_version` | Registration from the run |
| `services/ml_service/tests/test_promotion_integration.py` | Promotion against the live tracking server |

### Promotion

`POST /api/registered-models/{name}/versions/{version}/promote` registers a notebook-registered version as
a platform model.

| Body field | Default | Meaning |
|---|---|---|
| `feature_config_id` | required | The model's inputs |
| `equipment_id` | none | Equipment whose sensors drift reads; every base sensor must exist on it |
| `detector` | `sklearn` | What the output means ([ADR-0028](#adr-0028-dec-1-3)) |
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

### Registry endpoints

Auth as in [Endpoint auth](#endpoint-auth).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/registered-models`, `/api/registered-models/{name}` | token | Tenant-scoped MLflow registry read |
| PATCH | `/api/registered-models/{name}` | writer | Edit the description |
| POST | `/api/registered-models/{name}/versions/{version}/promote` | writer | Make a notebook-registered version a platform model ([promotion](#promotion)) |
| GET | `/api/experiments`, `/api/experiments/{name}/runs` | token | Tenant-scoped experiment read |

## ADR-0040 dec 5

A promotion registers under the version's name and version; `ml_models` holds `UNIQUE (model_name, model_version)`, so promoting the same version again is refused with 409.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/03-tenant-ml-tables.sql` | `unique_model_version` |
| `services/ml_service/api/routers/models.py::register_model` | Unique violation answered with 409 |

Links: [extensions](../operations/extensions.md) (domain transforms and detectors, [ADR-0008](../../ADR/ADR-0008-extension-interface-for-a-domain-generic-core.md), [ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)), model tracking and registry per tenant ([ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multi-tenancy.md), [ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway-and-the-tracking-capability.md)), stateful-feature kill-switch ([ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)), operator feedback and retrain recommendation ([ADR-0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md), [alerting](alerting.md)), externally-authored models ([ADR-0030](../../ADR/ADR-0030-admission-of-externally-authored-models.md), [model upload](model-upload.md)).
