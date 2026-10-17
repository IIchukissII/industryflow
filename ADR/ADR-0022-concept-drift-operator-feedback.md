<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0022 — Concept-drift operator feedback

- **Status:** Accepted
- **Date:** 2026-07-05; changed 2026-10-17
- **Parent:** [ADR-0021](ADR-0021-model-drift-monitoring.md)
- **Related:** [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (tenant-scoped roles),
  [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (tenant routing)

## Context

Label-free drift cannot say whether a model's alerts are still correct; that needs a human verdict on
fired alerts. Alerts expire after a retention period, and acknowledging means "seen", not "real". The
platform has no training service; a notebook trains and registers new versions.

## Decision

1. **An operator labels a fired alert true positive, false positive or unsure, distinct from
   acknowledge.** An operator holds a writing role in the tenant (ADR-0004 dec 5); a read-only user may
   read labels but not write them.
2. **Labels live in a per-tenant table without retention, never on the alerts table.** Each label
   copies the alert's rule, model, detection type and severity, so it outlives the alert.
3. **Precision and false-positive rate over time are derived; recall is not.** Fired-alert labels hold
   no missed events, so the surface never implies recall.
4. **The closed loop is a retrain recommendation, not automated training.** Sustained drift (a minimum
   count of drift alerts over a lookback) plus decayed precision (trailing-window precision below a
   configured floor, given a minimum of decided labels, compared with no earlier window) raises a
   high-severity statistical alert and marks the model until acknowledged.
5. **Same homes as ADR-0021.** Label write and precision read on the gateway's alert surface; the
   recommendation evaluator in the alert service, after each drift pass, with a per-model cooldown.
6. **Tenant-scoped and attributed.** Attribution records who labelled; decision 1 decides who may.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Label columns on the alerts table | Retention deletes the label with the alert |
| Reuse the per-prediction ground-truth column | Different grain and lifecycle |
| Compute recall from labels | Labels carry no missed events |
| An automated retrain executor now | No training service; its own ADR |
| A standalone labelling service | A small write on the existing alert surface |
| Any authenticated tenant user may label | A label is a durable write that moves precision |

## Consequences

**Positive**
- Alert triage becomes a durable quality signal that outlives alert retention.

**Negative**
- Precision alone is a partial performance picture.
- A model below the floor from the start counts as decayed.
- The recommendation is advisory until a training service exists.

## Deferred

| Open question | Settled by |
|---|---|
| Ground truth for missed events (recall) | A reconciliation source; needed before any accuracy claim |
| Automated retrain execution | A training-service ADR |
| Decay as a drop from an earlier precision window instead of a fixed floor | The maintainer, once labels have history |

## References

- [docs/architecture/alerting.md](../docs/architecture/alerting.md): labels, metrics, thresholds.
- `services/api_gateway/routers/alerts_history.py`, `services/alert_service/worker/`.
