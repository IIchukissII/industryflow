<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0041 — A model's feature configuration is fixed once bound

- **Status:** Accepted
- **Date:** 2026-10-29
- **Parent:** [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (one computation of a
  feature for training and serving)
- **Related:** [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (dec 1: the output
  contract fixed at registration), [ADR-0035](ADR-0035-drift-baseline-derived-scores.md) (dec 4: the
  sample checked against the bound inputs), [ADR-0040](ADR-0040-notebook-model-promotion.md) (dec 3: the
  promoting user binds the configuration), [ADR-0034](ADR-0034-platform-schema-migration-strategy.md)
  (the schema change)

## Context

A model is registered bound to a feature configuration: the sensors it reads and the transforms that turn
them into the vector it was trained on. Serving, the deployment gate and drift each read that
configuration afresh by reference at every use. A configuration is a shared, named record a tenant can
edit or delete, so an edit changes the input vector of every model bound to it on its next score, and
the drift sample checked against the old inputs stops describing them. Deleting it leaves its models
unbound and unservable. The output side of the same contract, the declared detector, is already fixed
at registration (ADR-0028).

## Decision

1. **A model's binding to its feature configuration is fixed at registration.** A model was trained on
   one input vector; a different one is a different model, registered or promoted again.
2. **A configuration bound by any model cannot change what it computes, and cannot be deleted.** Its
   sensors and transforms are fixed while a model binds it, whatever that model's status; a change is a
   new configuration. Name, description and equipment type stay editable because nothing computes from
   them.
3. **The refusal is the database's, and the API reports it with the models that bind the
   configuration.** Every writer of the shared record meets the same rule, and a check made only before
   the write could be raced.
4. **A configuration no model binds stays fully editable and deletable.** Removing its last model frees
   it.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Rebinding a registered model to another configuration | Serves a model a vector it was never trained on, and invalidates its drift sample without saying so |
| 2 | Versioned configurations, a model pinning a revision | A revision store and pinned reads for what a new configuration already gives; two models that differ in inputs still differ |
| 2 | The model copying the configuration at registration | Two different records answer "what is configuration X"; the shared one no longer says what any model computes |
| 2 | Editing in place, as before | An edit is a silent train/serve skew on every bound model (ADR-0023) |
| 2 | Deleting a bound configuration and unbinding its models | Leaves deployed models unservable with no decision by whoever deployed them |
| 2 | Freezing every field of a bound configuration | Name, description and equipment type change no computation |
| 3 | A check in the API alone | Races a concurrent bind, and every other writer of the record skips it |

## Consequences

**Positive**
- A model's input vector is the one it was trained on for as long as it exists.
- A drift sample accepted at registration matches its model's inputs for the model's lifetime.
- Configuration reload for stream-computed features (ADR-0023 deferral) has no bound configuration to
  reload.

**Negative**
- Changing the inputs of a deployed model means a new configuration and a new registration.
- Unused bound configurations accumulate until their models are removed.

## Deferred

| Open question | Settled by |
|---|---|
| None | — |

## References

- `infrastructure/timescaledb/init-scripts/30-bound-feature-config.sql`: decisions 2–4.
- `services/ml_service/api/routers/feature_configs.py`: the refusal reported (decision 3).
- [docs/architecture/ml-and-features.md](../docs/architecture/ml-and-features.md): the configuration API.
