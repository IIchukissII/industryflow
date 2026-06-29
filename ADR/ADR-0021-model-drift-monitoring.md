<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0021 — Model drift monitoring (the `statistical` alert lane)

- **ID:** ADR-0021
- **Status:** Accepted (design; implementation deferred)
- **Date:** 2026-06-28
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (pluggable detectors), [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multitenancy.md)/[ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (model registry + tracking). Realizes the alert-rule schema's reserved `statistical` detection type.

## Context and problem

The platform now trains, registers, and serves models (anomaly detectors driving alerts; notebook-authored models). A deployed model silently decays: equipment ages, seasons turn, set-points change, so the live data drifts away from what the model was trained on and its judgments quietly stop meaning what they did. Nothing today notices.

The alert service answers *"is this reading anomalous right now?"* — per-event, real-time, via the Kafka worker + ml-service inference. Drift is a different question on a different timescale: *"has the world moved away from this model's training distribution?"* — **windowed and statistical, not per-event.** Forcing it into the per-reading worker would be a category error.

Two facts already in the codebase shape the answer:
- the alert-rule schema already reserves **`detection_type='statistical'`** (`CHECK (detection_type IN ('threshold','ml','statistical'))`) — an alert lane built for exactly this and never wired;
- **`evidently`** is already an ml-service dependency (unused), and every model records its training baseline coordinates (`training_start_date`/`training_end_date`/`mlflow_run_id`/`training_metrics`).

So this is mostly *wiring foundations that already exist*, not new infrastructure. What is undecided — and what this ADR fixes — is the shape: what drift we detect, who owns it, how it surfaces, and what the baseline is.

## Decision drivers

- **Reuse the existing surfaces.** The `statistical` alert lane, `evidently`, the model metadata, and the alert history/severity/notification machinery should carry drift — not a parallel system.
- **Label-free first.** Sensor streams are unlabeled in real time; the v1 must detect drift *without ground truth*, with labelled performance drift as a later, bigger step.
- **One detection home, ML math where the ML stack is.** Threshold/ml/statistical detection should live in one place (the alert service); the heavy statistical compute belongs where `evidently` and the tenant data path already are (ml-service) — exactly the split the real-time ml rules already use.
- **Tenant isolation (ADR-0003/0011).** Drift is evaluated per model, per tenant, over tenant-scoped data, against a per-tenant baseline.
- **Decisions before implementation.** This ADR is the decision; code follows in a later pass.

## Decision

1. **v1 detects label-free drift: data drift + prediction drift.** *Data drift* — the input-feature distribution of a recent window vs the model's training baseline (`evidently`). *Prediction drift* — a shift in the model's own output distribution (anomaly rate / score distribution) over time. Both need no ground truth. **Performance/concept drift** (precision/recall decay) needs operator labels and is **deferred** (see below).

2. **Drift surfaces through the reserved `statistical` alert lane.** A drift rule (`detection_type='statistical'`, bound to a model + a drift threshold) raises a normal alert — *"model `vpd-forecaster` input drift 0.42 > 0.30 — consider retraining"* — into the same alert history, severity, and notification path as threshold/ml alerts. No new alerting surface; the Models page may later show a drift indicator from the same signal.

3. **The alert service owns drift detection; ml-service computes the drift score.** A **scheduled drift evaluator runs in the alert service beside the real-time rules engine** — keeping all detection (threshold/ml/statistical) in one home: it schedules evaluations, applies each rule's threshold, and fires the alert. The **statistical computation is delegated to ml-service** over an internal endpoint (windowed tenant data + reference profile → `evidently` → drift score), exactly mirroring how the real-time ml rules already delegate scoring to ml-service `/api/inference`. The alert service stays the decision/alert home without duplicating `evidently` or the tenant data path.

4. **A reference profile is captured at training time and stored with the model.** Drift is meaningless without a baseline, so when a model is trained/registered the platform records a compact distribution snapshot (column statistics / an `evidently` reference) over the training window, stored **with the model and tenant-scoped** — an MLflow artifact for tracked models (via the ADR-0019 gateway), or the `ml_models` training metadata otherwise. A model with no reference profile reports *drift unavailable* until retrained with one, rather than guessing a baseline from possibly-compacted history.

5. **Windowed and periodic, not per-event.** Drift is evaluated on a schedule (cadence configurable) over a trailing window, independent of the per-reading anomaly path. The two never share a code path; they share only the alert sink.

6. **Tenant-scoped throughout.** Each evaluation is for one model in one tenant, reads that tenant's data through the existing scoped path, and compares against that tenant's model's reference profile. No cross-tenant baseline or evaluation.

## Alternatives considered

- **Detect drift in the real-time Kafka worker.** *Rejected:* drift is windowed/statistical; per-event evaluation is the wrong timescale and would either be wrong or hammer the system.
- **A standalone drift microservice.** *Rejected for now:* drift is a detection concern and the alert service is the detection home; a new service adds an operational surface for no isolation or scaling benefit the alert-service evaluator + ml-service compute don't already give.
- **Compute drift entirely inside the alert service.** *Rejected:* it would duplicate `evidently` and the tenant data-read path that already live in ml-service; delegating the score (as the real-time ml rules do) keeps one ML home.
- **Fuse drift into the Spark aggregation layer.** *Considered:* attractive if drift windows align with the heavy aggregations, but it splits detection ownership away from the alert service and couples drift cadence to the Spark job; kept as a possible compute backend, not the v1 owner.
- **Ship labelled performance drift now.** *Rejected for v1:* it needs an operator feedback/labelling loop and a label store — a separate, larger initiative; data+prediction drift delivers value without it.

## Consequences

### Positive
- Silent model decay becomes a visible, actionable alert on the surface operators already watch, reusing the reserved `statistical` lane and the dormant `evidently` dependency.
- One detection home (threshold/ml/statistical) with ML math delegated to the one ML service — consistent with the existing real-time pattern.
- Tenant-isolated, baseline-grounded, and label-free, so it ships without a feedback UI.

### Negative
- Requires a reference profile per model; models predating this report *drift unavailable* until retrained (acceptable, honest).
- Adds a scheduled evaluator to the alert service and an internal drift endpoint to ml-service (new surfaces, but small and pattern-consistent).
- Data/prediction drift signals *distribution change*, not *performance loss* — a drift alert means "investigate/consider retraining," not "the model is wrong." The copy must say so to avoid alarm fatigue.

## Deferred decisions

- **Performance/concept drift via operator feedback.** Labelling alerts (true/false), a label store, precision/recall-over-time, and the closed-loop **auto-retrain trigger** (note: the `/api/training` stub was removed — retraining is manual/notebook today). The largest follow-on.
- **Reference-profile serialization + storage location** per model source (MLflow artifact vs `ml_models` metadata vs a per-tenant table), and how the notebook/training flow emits it.
- **Drift method + thresholds + cadence.** Which `evidently` tests/metrics, default thresholds, evaluation window, and schedule — configuration, not architecture.
- **Models-page drift indicator.** Surfacing the same signal in the Models UI beside metrics/versions.
- **Compute backend.** Whether the ml-service endpoint computes inline or offloads heavy windows to Spark.

## References
- Reserved `statistical` detection type: `infrastructure/timescaledb/init-scripts/02-tenant-alert-tables.sql`.
- Existing real-time ml delegation: `services/alert_service/worker/rules_engine.py` → ml-service `/api/inference/predict`.
- `evidently` (unused dep): `services/ml_service/api/requirements.txt`. Model baseline metadata: `infrastructure/timescaledb/init-scripts/03-tenant-ml-tables.sql`.
- ADR-0010 (pluggable detectors), ADR-0013/0019 (registry + tracking), ADR-0011 (notebook/model arc).
