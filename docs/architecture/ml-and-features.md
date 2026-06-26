<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ML & feature engineering

How IndustryFlow turns raw sensor readings into anomaly scores: configurable feature
engineering, MLflow-tracked models, and real-time inference — all **domain-generic**, with
domain specifics supplied as [extensions](../operations/extensions.md). Code under
`services/ml_service/api/` is authoritative.

## Feature engineering (configuration-driven)

A model's input features are defined by a **feature configuration** stored per tenant in
`feature_engineering_configs` (not in code). A config lists `base_sensors` and an ordered list
of `transformations`, each with a `type` and params. The `FeatureEngineeringEngine` reads the
config and produces the feature vector; it holds no fixed feature list and no domain knowledge.

Each transformation `type` is resolved through the **extension registry**
(**[ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)**). The platform ships generic
built-ins — `identity`, `polynomial`, `interaction`, `deviation`, `statistical`,
`rolling_stat` — and a domain registers new transform types in its own module without editing
the engine. Window statistics (e.g. deviation from a rolling mean) come from a Redis-backed
**feature store** keyed by equipment + sensor.

## Models & MLflow

Models are tracked in **MLflow** (experiments, runs, metrics) with artifacts in MinIO/S3. The
ML service exposes models, experiments/runs, and feature configs, and records each model's
`feature_config_id` so inference knows how to engineer its inputs. (Automated end-to-end
training from the UI is not yet wired — models are produced via the MLflow/notebook workflow.)

## Inference

`POST /api/inference/predict`:

1. Resolves the tenant from the verified identity and loads the model record + its feature
   config.
2. Engineers the feature vector from the incoming sensor data (feature store for windowed
   stats).
3. **Scores** the features through the **anomaly-detector registry**: the model record's
   `detector` (default `sklearn`, the built-in that handles IsolationForest / XGBoost /
   `predict_proba` / direct-score models) returns a 0–1 score and a threshold decision. A
   domain can register a custom or model-free detector without touching the core scorer
   (**[ADR-0010](../../ADR/ADR-0010-extension-plugin-mechanism.md)**).

The alert detection worker calls inference for ML-based rules using the shared
`INTERNAL_SERVICE_TOKEN` (it has no human to log in); see
**[authentication.md](../operations/authentication.md)** and
**[alerting.md](alerting.md)**.
