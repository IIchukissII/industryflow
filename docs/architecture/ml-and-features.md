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
ML service exposes models (including a tenant-scoped registered-models read path) and feature configs, and records each model's
`feature_config_id` so inference knows how to engineer its inputs. (Automated end-to-end
training from the UI is not yet wired — models are produced via the MLflow/notebook workflow.)
At registration a model may also carry a **`reference_profile`** — a compact, tenant-scoped
sample of its training-window distribution — which is the baseline for drift monitoring below
(**[ADR-0021](../../ADR/ADR-0021-model-drift-monitoring.md)**).

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

Loaded models are held in a **process-local warm cache** keyed by MLflow run_id, so the
anomaly detector and the drift evaluator reuse a warm model instead of cold-loading from
MLflow on every call (bounded LRU, `MODEL_CACHE_SIZE`).

## Model drift monitoring

A deployed model silently decays as equipment ages and conditions change: the live data drifts
from what it was trained on and its judgments quietly stop meaning what they did. Drift is a
different question from real-time anomaly detection — *"has the world moved away from this
model's training distribution?"* — **windowed and statistical, not per-event**
(**[ADR-0021](../../ADR/ADR-0021-model-drift-monitoring.md)**).

- **Baseline.** At registration a model stores a `reference_profile`: a compact,
  deterministically down-sampled, tenant-scoped sample of its training-window columns
  (optionally including the model's own output scores). A model without one reports *drift
  unavailable* until retrained — honest, rather than guessing a baseline.
- **Compute.** `POST /api/drift/evaluate` reads a trailing window of the tenant's recent data,
  compares it against the reference with **`evidently`**, and returns **data drift** (input
  distributions) and **prediction drift** (the model's output distribution). The primary scalar
  is the drifted-feature *share*.
- **Ownership.** The **alert service owns detection** (one home for threshold/ml/statistical)
  and delegates the statistical compute here — exactly as the real-time ml rules delegate to
  `/api/inference`. The drift signal surfaces through the reserved `statistical` alert lane; see
  **[alerting.md](alerting.md)**.
- **Label-free first, then operator feedback.** v1 needs no ground truth. Performance/concept
  drift is added by **[ADR-0022](../../ADR/ADR-0022-concept-drift-operator-feedback.md)**:
  operators label fired alerts *real / false / unsure* (a durable per-tenant `alert_labels`
  store, separate from the 90-day alerts hypertable), from which the platform derives
  **precision + false-positive rate over time** (`GET /api/alerts/label-metrics`) and raises a
  **retrain *recommendation*** when a model shows sustained drift **and** precision decay.
  Recall is not derivable from fired-alert labels (no false-negative signal), and the loop
  *recommends* — it does not train, since retraining is manual/notebook today. See
  **[alerting.md](alerting.md)**.
