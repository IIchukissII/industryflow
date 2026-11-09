<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0022 — Concept-drift operator feedback

- **Status:** Accepted
- **Date:** 2026-07-05; changed 2026-11-09
- **Parent:** [ADR-0021](ADR-0021-model-drift-monitoring-the-statistical-alert-lane.md)
- **Related:** [ADR-0004](ADR-0004-api-authentication-session-model-and-transport-security.md) (tenant-scoped roles),
  [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (tenant routing),
  [ADR-0044](ADR-0044-a-model-is-scored-on-complete-computed-input-or-not-at-all.md) (dec 3: a degraded score recorded on its alert),
  [ADR-0033](ADR-0033-tenant-lifecycle.md) (dec 1, 4: the label table is the tenant's, and leaves with it)

## Context

Label-free drift cannot say whether a model's alerts are still correct; that needs a human verdict on
fired alerts. Alerts expire after a retention period, and acknowledging means "seen", not "real". The
platform has no training service; a notebook trains and registers new versions.

## Decision

1. **An operator labels a fired alert true positive, false positive or unsure, distinct from
   acknowledge.** An operator holds a writing role in the tenant (ADR-0004 dec 5); a read-only user may
   read labels but not write them.
2. **Labels live in a per-tenant table without retention, never on the alerts table.** Each label
   copies the alert's rule, model, detection type, severity and whether its score was degraded
   (ADR-0044 dec 3), so it outlives the alert; it leaves with its tenant (ADR-0033 dec 1, 4).
3. **Precision and false-positive rate over time are derived; recall is not.** Fired-alert labels hold
   no missed events, so the surface never implies recall. Labels on degraded alerts are reported
   apart: they judge a score the model was not fully given. A model's precision counts only labels on
   alerts its own score raised; labels on its drift alerts and recommendations judge the monitoring,
   not the model, and are reported apart.
4. **The closed loop is a retrain recommendation, not automated training.** Sustained drift (a minimum
   count of drift alerts over a lookback) plus decayed precision (trailing-window precision below a
   configured floor, given a minimum of at least one decided label on the model's own alerts that
   were not degraded, compared with no earlier window) raises a high-severity statistical alert and
   marks the model until acknowledged; no second recommendation is raised while one is open.
5. **Same homes as ADR-0021.** Label write and precision read on the gateway's alert surface; the
   recommendation evaluator in the alert service, after each drift pass, with a per-model cooldown.
6. **Tenant-scoped and attributed.** Attribution records who labelled; decision 1 decides who may.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Any authenticated tenant user may label | A label is a durable write that moves precision and can raise a recommendation; a read-only role would not hold |
| 2 | Label columns on the alerts table | Retention deletes the label together with its alert |
| 2 | Reuse the per-prediction ground-truth column | A different grain and lifecycle from a verdict on a fired alert |
| 3 | Labels on degraded alerts counted with the rest | A database incident would read as the model's precision falling |
| 3 | Compute recall from labels | Fired-alert labels carry no missed events |
| 4 | An automated retrain executor now | No training service exists to invoke; it needs its own ADR |
| 3, 4 | A model's precision over every label carrying its id, drift alerts and recommendations included | A verdict on a recommendation would move the precision that raises the next one, and a verdict on drift judges the distribution, not the model |
| 4 | Labels on degraded alerts in the decay test | A kill-switch episode could raise a retrain recommendation for a model that did not decay |
| 4 | Severity of the recommendation as a setting | A deployment could contradict the decided severity |
| 4 | A new recommendation each cooldown while the first is open | The model would stay marked until every repeat is acknowledged; a repeat adds nothing |
| 5 | A standalone labelling service | Labelling is a small write on the existing alert surface |

## Consequences

**Positive**
- Alert triage becomes a durable quality signal that outlives alert retention.

**Negative**
- Precision alone is a partial performance picture.
- A model below the floor from the start counts as decayed.
- The recommendation is advisory until a training service exists.
- A label recorded before degradation was recorded counts with ordinary labels.
- A recommendation nobody acknowledges is not renewed while the model keeps decaying.
- Alert retention removing an unacknowledged recommendation unmarks the model, and the next pass may
  raise a new one.

## Deferred

| Open question | Settled by |
|---|---|
| Ground truth for missed events (recall) | A reconciliation source; needed before any accuracy claim |
| Automated retrain execution | A training-service ADR |
| Decay as a drop from an earlier precision window instead of a fixed floor | The maintainer, once labels have history |

## References

- [docs/architecture/alerting.md](../docs/architecture/alerting.md): labels, metrics, thresholds;
  manual of decisions 1-6.
