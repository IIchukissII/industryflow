<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0023 — Stream-materialized feature engineering

- **Status:** Accepted
- **Date:** 2026-07-06; changed 2026-10-17
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md) (framing)
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (tenant routing, shared contract), [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (watermarked windows, window contract), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (transform registry), [ADR-0024](ADR-0024-stateful-feature-kill-switch.md) (kill-switch for the stateful class)

## Context

Stateful feature transforms need a windowed statistic of a sensor's recent history; the stream already
keeps a watermarked state store and materializes per-tenant windowed aggregates. A second windowing
mechanism on the serving side would compute the same value differently, and training and serving would
disagree about what a feature means. Offline training data cannot read the live aggregate table.

## Decision

1. **Stateful, windowed features compute in the stream's existing state store.**
2. **A materialized per-tenant table is the seam between producing and consuming features.** For the
   windowed baseline it is the existing aggregate tables; a dedicated table only for what they lack.
3. **Stateless transforms stay substrate-neutral; they run wherever is cheapest.**
4. **Tenant isolation is the stream partition key; output goes to the tenant schema (ADR-0003).**
5. **Per-tenant feature configuration is routed live within the shared stream, without a restart.**
6. **The transform registry holds across the stream's executor boundary** (ADR-0010).
7. **The serving side holds no windowing state.** The baseline is a read of the aggregate, fronted by a
   short-lived, bounded, tenant-keyed cache of returned values; the alerting worker's tenant-keyed
   latest-value store is a snapshot join and outside this decision.
8. **The rest is built when a feature needs it:** stream-computed features (decisions 4–6), a dedicated
   feature table, the rolling-statistic transform.
9. **Offline training re-derives the served window.** Each row gets the newest window written by its
   instant given the allowed lateness, with width and lateness defined once in the shared contract and the
   statistic named explicitly; a test asserts offline and serving agree on the same data.

## Alternatives

| Alternative | Why rejected |
|---|---|
| A serving-side windowing store beside the stream | Duplicated state; train/serve skew |
| Rolling features computed in the serving process | Recreates a windowing engine next to the existing one |
| Inference moved into the stream as well | Per-tenant artifacts and blast radius; a separate decision |
| One feature table keyed by a tenant column | Breaks schema-per-tenant isolation |
| Training bound by granularity alone | The lateness gap reintroduces skew |

## Consequences

**Positive**
- One windowing substrate with structural tenant isolation; live training and inference read the same rows.

**Negative**
- The offline re-derivation is a second implementation, kept equal only by its test.
- Live per-tenant configuration needs a reload mechanism that does not exist yet.
- A windowed feature exists only after its window is written; the cache adds seconds of staleness.
- Changing a window's width or lateness requires retraining the models that use it.

## Deferred

| Open question | Settled by |
|---|---|
| Configuration reload mechanism (decision 5) | The first feature computed in the stream |
| Dedicated feature table schema and retention | The first feature the aggregates cannot provide |

## References

- [docs/architecture/ml-and-features.md](../docs/architecture/ml-and-features.md); baseline read
  `services/ml_service/api/feature_engineering/baseline_provider.py`; window contract
  `libs/industryflow_contracts/`; agreement test `services/ml_service/tests/test_train_serve_agreement.py`
