<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0022 — Concept-drift operator feedback (alert labels, precision-over-time, retrain recommendation)

- **ID:** ADR-0022
- **Status:** Accepted (implemented — label store + precision-over-time + retrain recommendation + operator UI; box-validated) — rev 2
- **Date:** 2026-07-05 (rev 2: 2026-07-05)
- **Project:** IndustryFlow
- **Parent:** [ADR-0021](ADR-0021-model-drift-monitoring.md) (model drift monitoring — realizes its top deferred decision)
- **Companions:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (pluggable detectors), [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multitenancy.md)/[ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (model registry + tracking).

## Context and problem

ADR-0021 shipped **label-free** drift: data drift and prediction drift, both computed without any ground truth, surfaced through the `statistical` alert lane. It deliberately deferred the hardest, most valuable follow-on (ADR-0021 deferred decisions, bullet 1): *performance/concept drift via operator feedback — labelling alerts true/false, a label store, precision/recall-over-time, and the closed-loop retrain trigger.* This ADR fixes the shape of that follow-on.

Label-free drift answers *"has the input distribution moved?"* It cannot answer *"are this model's alerts still correct?"* — that needs a human verdict on fired alerts. Two facts constrain the design:

- The `alerts` table (`02-tenant-alert-tables.sql`) is a **TimescaleDB hypertable with a 90-day retention policy** and already carries *acknowledge* fields (`acknowledged`/`acknowledged_at`/`acknowledged_by`) — a binary "seen" flag, **not** a correctness verdict. Acknowledge and "was this alert real?" are different questions.
- There is **no training service.** The `/api/training` stub was removed (ADR-0021); a model gets a new version when a notebook trains it and calls `POST /api/models` with a fresh `reference_profile`. Nothing in the platform can *execute* a retrain today.

So the undecided shape is: what an operator labels, where the label lives (given the alert itself expires), what performance signal we can honestly derive, and what "closed-loop retrain" can mean when no trainer exists.

## Decision drivers

- **Reuse ADR-0021's homes.** Labels ride the alert surface operators already use (acknowledge → label is the same gesture on the same row); precision-over-time is a read over those labels; the retrain evaluator is a sibling of the existing scheduled drift evaluator. No parallel system.
- **The label must outlive the alert.** A label is the durable quality/training signal; it cannot share the alert's 90-day retention or it evaporates exactly when it becomes historically useful.
- **Honesty over false completeness.** Report only the performance metric fired-alert labels can actually support, and do not pretend to auto-train when there is no trainer.
- **Tenant isolation (ADR-0003/0011).** Labels, metrics, and recommendations are per tenant, over tenant-scoped data, with the acting operator recorded.

## Decision

1. **Operators label a fired alert with a correctness verdict — `true_positive` | `false_positive` | `unsure` — distinct from acknowledge.** Acknowledge means "I have seen this"; a label means "this alert was (not) a real event." One current verdict per alert, re-labelable (an upsert), with an optional free-text note and the acting operator recorded. The write mirrors the existing `PATCH /api/alerts/{alert_id}/acknowledge` on the api_gateway surface the frontend already talks to.

2. **Labels persist in a dedicated per-tenant `alert_labels` table, never as columns on the `alerts` hypertable.** The alerts hypertable drops chunks at 90 days; the label must survive that. `alert_labels` is an ordinary (non-hypertable, no-retention) per-tenant table keyed by `alert_id`, and it **denormalizes** the labelled alert's `rule_id`, `model_id`, `detection_type`, and `severity` at label time so the verdict stays self-describing and groupable after the source alert row has aged out.

3. **v1 derives precision and false-positive rate over time; true recall is out of reach and stays deferred.** From labelled fired alerts we can compute, per rule and per model over a window, precision = TP / (TP + FP) and the false-positive rate and volume trend. **Recall is not computable from fired-alert labels alone** — we never observe the anomalies that *didn't* fire (the false negatives), so any "recall" from this data would be a fiction. Recall requires a separate ground-truth source and is explicitly deferred; the metric surface must not imply otherwise.

4. **The closed loop is a retrain *recommendation*, not automated training.** When a model shows **sustained** label-free drift (ADR-0021) **and** a label-derived precision decay, the platform raises a *"retrain recommended"* signal — a distinct high-severity `statistical` alert plus a derived flag surfaced beside the model — telling an operator to retrain via the notebook flow. It does **not** train the model: there is no training service to invoke, and building one is a separate initiative (a future ADR). "Closed-loop" here means *detect → recommend → surface*, with *execute* left to a human until a trainer exists. This is the honest ceiling of the current architecture.

5. **Same split of homes as ADR-0021.** The label write and the precision-over-time read live on the api_gateway alert surface (tenant-scoped, authenticated operator); the retrain-recommendation evaluator is a sibling of the scheduled drift evaluator in the alert service, reusing its internal→ml-service auth path. No new service.

6. **Tenant-scoped and attributed throughout.** Every label, metric, and recommendation is for one tenant, written through the existing schema-routed path with the acting operator recorded in `labeled_by`. No cross-tenant read or aggregation.

## Alternatives considered

- **Add label columns to the `alerts` table.** *Rejected:* the 90-day retention policy would drop each label together with its alert — losing the signal precisely when it gains historical value. A separate table decouples label lifetime from alert lifetime.
- **Reuse `model_predictions.actual_label`.** *Rejected:* that column is per-*prediction* supervised ground truth for a scored row, a different grain and lifecycle from an operator's verdict on a fired *alert*. It stays the home for prediction-level labelling; alert feedback is its own thing.
- **Compute recall from operator labels.** *Rejected as impossible:* fired-alert labels contain no information about missed events (false negatives). Presenting a recall number here would mislead.
- **Build an auto-retrain executor now.** *Rejected:* no training service exists (the `/api/training` stub was removed). An honest closed loop recommends and surfaces; automated execution needs a trainer and its own ADR.
- **A standalone feedback/labelling microservice.** *Rejected:* labelling is a small write on the alert row; the alert surface is its natural home, exactly as ADR-0021 kept drift in the alert service rather than spinning up a service.

## Consequences

### Positive
- Turns the alerts operators already triage into a durable, tenant-scoped quality signal, reusing the acknowledge gesture and the `statistical` lane — no new operator surface.
- Labels outlive the 90-day alert window, so precision-over-time and retrain recommendations can look back across model generations.
- The closed loop is honest: it recommends retraining on real evidence (drift *and* precision decay) without pretending to a training capability the platform does not have.

### Negative
- Adds one per-tenant table (`alert_labels`) and a migration that must backfill existing tenants and wire into tenant creation.
- Precision-only (no recall) is a partial performance picture; the copy and API must state the limitation so it is not read as full model evaluation.
- The retrain recommendation is advisory — a human must still act on it — so the loop is "closed" only in the detect-and-recommend sense until a training service lands.

## Deferred decisions

- **Recall / false-negative ground truth.** A mechanism to observe missed events (e.g. post-hoc incident reconciliation) that would make recall real. Needed before any "model accuracy" claim.
- **Automated retrain execution.** A training service the recommendation could invoke — its own ADR (registry write, MLflow run creation, the tenant data/training path, reference-profile emission).
- **Recommendation thresholds + cadence.** How much sustained drift and how much precision decay trigger a recommendation, and how often the evaluator runs — configuration, tuned after the label store has real data.

## Implementation status (rev 2)

All slices implemented and **box-validated** on the live compose stack (label + label-metrics exercised end-to-end over HTTP; the retrain evaluator runs on the drift schedule):

- **Slice 1 — label store.** `alert_labels` per-tenant table (migration `15-alert-labels.sql`), wired into `create_tenant_schema()` and backfilled onto existing tenants; verdict `CHECK`-constrained, denormalized rule/model/detection/severity, one re-labelable verdict per alert. `PATCH /api/alerts/{alert_id}/label` (mirrors acknowledge) + the label surfaced on the alert read path.
- **Slice 2 — precision over time (dec 3).** `GET /api/alerts/label-metrics` buckets labels by time and returns precision + false-positive rate, per bucket and overall, scoped by model/rule. Recall is deliberately absent (a `note` says so); `unsure` is excluded from the denominator.
- **Slice 3 — retrain recommendation (dec 4).** A scheduled evaluator in the alert worker (sibling of the drift evaluator, same cadence) raises a distinct high-severity `statistical` alert (`condition='retrain_recommended'`) when a model shows BOTH sustained drift (recent drift alerts) AND label-derived precision decay — model-scoped cooldown so it does not repeat every cycle. It recommends; it does not train (no training service — dec 4).
- **Slice 4 — operator UI.** The Alert History page has a per-alert three-way label control (Real / False / Unsure → `PATCH …/label`) and a "Precision (labelled)" KPI reading `label-metrics`; the same page also wires the acknowledge action. Distinct from acknowledge; worded, not colour-only.

Still open (not built): the surfaced retrain flag beside the model on the Models page (the recommendation lands in alert history today).

## References
- ADR-0021 (model drift monitoring) — parent; this realizes its deferred operator-feedback decision.
- Alerts hypertable + 90-day retention, reserved `statistical` type: `infrastructure/timescaledb/init-scripts/02-tenant-alert-tables.sql`.
- Acknowledge pattern mirrored by the label write: `services/api_gateway/routers/alerts_history.py`.
- Prediction-level label precedent (different grain): `model_predictions` / `labeled_predictions` in `infrastructure/timescaledb/init-scripts/03-tenant-ml-tables.sql`.
- Scheduled drift evaluator the retrain evaluator siblings: `services/alert_service/worker/rules_engine.py` (`evaluate_drift_rules`).
