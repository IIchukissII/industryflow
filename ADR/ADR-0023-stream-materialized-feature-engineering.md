<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0023 — Stream-materialized feature engineering (one windowing substrate, the feature table as the seam)

- **ID:** ADR-0023
- **Status:** Proposed
- **Date:** 2026-07-06
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant routing), [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (Kafka delivery semantics), [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (Spark windowing & idempotent writes), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extension/plugin mechanism).

## Context and problem

The platform computes engineered features in two places that do not know about each other, and for windowed (stateful) features they compute overlapping things through different substrates.

- The **feature engine** (`services/ml_service/api/feature_engineering/engine.py`) is config-driven and row-wise: `transform()` takes a single `sensor_data` snapshot and returns an ordered feature vector, dispatching each transform type through the extension registry (ADR-0010). It holds no transform knowledge itself. Stateless transforms — `identity`, `polynomial`, `interaction`, `deviation` (`extensions/builtins.py`, a sibling package of `feature_engineering/`) — are pure functions of the current snapshot.
- The **stateful** transforms are different. `statistical` (`deviation_from_run_mean`) does not compute a window; it does a read-side lookup against the **FeatureStore** (`feature_engineering/feature_store.py`), which is a **Redis ring buffer** — the last ~100 readings per `equipment:sensor`, TTL 1h — with `compute_rolling_mean(window=50)`. `rolling_stat` is an unimplemented placeholder.
- Meanwhile the **Spark aggregation job** (`services/spark_jobs/kafka_aggregations.py`) already maintains a watermarked streaming **state store** to compute 1 min / 5 min / 1 hour roll-ups into TimescaleDB (ADR-0006).

So there are **two independent windowed-state mechanisms** — Spark's state store and the Redis FeatureStore — computing the same *kind* of thing (a rolling statistic over a window) through unrelated substrates. Two further problems follow from the split. First, **train/serve skew**: training reads history one way and inference reads the Redis ring buffer another, with no guarantee the two agree. Second, `rolling_stat` is unimplemented precisely because there was no stream substrate to compute it against; the Redis buffer is a bounded ring, not a windowing engine.

There is no *bug* here today — `statistical` works off Redis and the aggregation job works off Spark. This ADR does not order a rewrite. It records the **direction** for stateful feature computation so the decision is not re-derived from scratch when the first real windowed feature is built, and states the sharp trigger that should move it from proposed to implemented.

## Decision drivers

- **One windowing substrate, not two.** The platform already runs a watermarked Spark state store (ADR-0006). A second windowing engine (the Redis ring buffer) computing overlapping rolling statistics is duplicated state, not added capability. The stateful feature path should reuse the substrate that already exists rather than maintain a parallel one.
- **Kill train/serve skew by construction.** If features are computed once and materialized, training and inference read the *same* feature rows. Skew is removed by there being a single computation path, not by keeping two paths in sync.
- **Tenant isolation should be structural, not incidental.** The Redis key `features:{equipment_id}:{sensor_name}` carries no `company_id`; tenant isolation rests implicitly on `equipment_id` being a non-colliding UUID. A Spark group key makes the tenant boundary explicit.
- **Decouple production from consumption.** Whoever computes features and whoever reads them (training, inference) should meet at a contract, not a shared code path — the same "decoupled by default, bound by contract" discipline applied elsewhere.
- **Record the direction lazily; build on a trigger.** Nothing here needs code today. The value now is a written decision so the design is not lost; the code lands when a real windowed feature demands it.

## Decision

1. **Stateful, windowed features compute in Spark's existing state store — not a second Redis window.** When real rolling/lag/windowed features are implemented, they are computed in the watermarked streaming layer established by ADR-0006, reusing that state store rather than introducing or extending an independent windowing mechanism.

2. **A materialized feature table is the seam.** Spark *produces* engineered features into a per-tenant feature table (`tenant_<uuid>`, alongside the existing measurement/aggregate tables); training (notebooks) and inference (ml_service) *consume* from it. Production and consumption are decoupled by default and bound only by the table contract. Writes are idempotent on the natural key per ADR-0006 dec 3.

3. **Stateless, row-wise transforms stay substrate-neutral.** `identity`, `polynomial`, `interaction`, `deviation` and their kind are pure functions of a snapshot and do not require Spark; they may be computed wherever is cheapest (ingestion path, stream, or on demand). This ADR does not force them into the stream — only the stateful class benefits from the move.

4. **Tenant isolation is the stream partition key.** Windowed features group by `(company_id, equipment_id, sensor, window)`, so per-tenant state is partitioned structurally by the state store and written to the tenant schema via the existing `company_id_to_schema` routing (ADR-0003). This replaces the current implicit-via-UUID keying of the Redis store with an explicit tenant partition — an isolation upgrade, not merely a port.

5. **Per-tenant feature configuration is routed live within the shared stream.** A mixed-tenant micro-batch applies each tenant's `transformations` set to that tenant's rows (`applyInPandas` keyed by tenant), with the per-tenant config picked up without a stream restart (config topic / broadcast state). A config edit changes future output without redeploying the job. This is the real work the move requires; isolation (decision 4) is free, config liveness is not.

6. **The ADR-0010 transform registry must hold across the Spark executor boundary.** Domain transform modules are shipped to executors (`--py-files`) and the registry is populated there, so `get_transform` resolves the same built-in and domain transforms inside Spark as it does inside ml_service. The ADR-0010 extension contract now spans the executor boundary, not just a single FastAPI process.

7. **The Redis FeatureStore is demoted, not duplicated.** Once features are materialized (decision 2), Redis ceases to be a second *computation* path. It either disappears or shrinks to a low-latency *serving cache* read from the materialized table. There is one source of truth for a feature value and, at most, a cache in front of it.

8. **Trigger and scope.** This is recorded now as direction; implementation lands when the **first real windowed feature requires it**, or when train/serve skew is observed, or when the Redis FeatureStore becomes an operational burden. Until a trigger fires there are no code changes: `rolling_stat` stays a stub and `statistical` keeps its current Redis path. This ADR is the sharp failure condition, not a build order.

## Alternatives considered

**A. Keep the two windowing substrates (status quo).** *Rejected:* Spark's state store and the Redis ring buffer compute overlapping rolling statistics through unrelated mechanisms — duplicated state and a standing train/serve-skew risk. Keeping both is maintaining two answers to one question. (Accepted *temporarily* only until decision 8's trigger, since nothing is broken today.)

**B. Compute rolling features in ml_service Python over fetched history.** *Rejected:* it recreates a windowing engine in the serving layer, over a DB or Redis read, in parallel to the Spark state store that already exists — the same duplication as A, relocated. The stateful class belongs in the substrate already doing windowing.

**C. Fold model *inference* into Spark at the same time.** *Rejected here as out of scope:* inference carries a per-tenant model artifact and a blast-radius coupling (one tenant's slow/failing model stalls the shared batch) that feature transforms do not — a transform is uniform code with nothing to load and nothing to fail slowly. Inference-in-stream is a separate decision with a different cost profile and is not bundled into this one.

**D. Materialize into one shared feature table keyed by tenant column, not per-tenant schemas.** *Rejected:* it breaks the schema-per-tenant isolation the platform is built on (ADR-0003) and the tenant-reader role model. Features follow the same per-schema routing as measurements and aggregates.

## Consequences

### Positive

- One windowing substrate: the Redis ring buffer stops being a parallel windowing engine, and stateful features reuse the Spark state store the platform already runs.
- Train/serve skew is removed by construction — training and inference read the same materialized feature rows.
- `rolling_stat` becomes implementable, because there is now a stream substrate to compute it against.
- Tenant isolation for features moves from implicit (non-colliding UUID) to explicit (stream partition key + tenant schema).
- Feature production and consumption are decoupled behind a table contract, consistent with the platform's seam discipline.

### Negative

- Live per-tenant config routing in a shared stream (decision 5) is real work: picking up a tenant's edited `feature_config` without restarting the job needs a config-topic/broadcast-state mechanism that does not exist yet.
- The ADR-0010 registry must be shipped to and populated on executors (decision 6); the extension contract now spans the Spark boundary and must be tested there, not only in-process.
- Materialize-on-close raises feature latency versus an on-demand Python compute: a windowed feature appears when its window closes (plus watermark lag), not instantly on request — the same trade ADR-0006 dec 2 already makes for aggregates.
- A migration exists for any feature currently served from Redis if/when it moves; the demotion of the FeatureStore (decision 7) is a change to a live read path.

## Deferred decisions

- **Config-reload mechanism.** Config topic vs broadcast state vs periodic reload for decision 5, and the freshness guarantee a config edit gets, are implementation choices left to the build.
- **Feature table schema and retention.** Column shape, natural key, and retention/compression for the materialized feature table are a data-model task (companion to the ADR-0006 aggregate tables), not fixed here.
- **Redis: removed or demoted.** Whether the FeatureStore is deleted or retained as a serving cache over the materialized table (decision 7) is decided when the first feature actually moves.
- **Feature window horizons.** Allowed-lateness and window definitions for feature windows inherit ADR-0006's watermark discussion and are configured with the job, not fixed here.

## References

- ADR-0001 — framing; the generic-platform scope under which feature engineering is a platform capability and domain transforms are extensions.
- ADR-0003 — tenant-to-schema resolution; the `company_id_to_schema` routing and schema-per-tenant isolation decisions 4 and D rely on.
- ADR-0005 — Kafka delivery semantics; at-least-once, which the materialized-write idempotency assumes.
- ADR-0006 — Spark windowing & idempotent writes; the watermarked state store and idempotent-upsert discipline this decision reuses for features.
- ADR-0010 — extension/plugin mechanism; the transform registry whose contract decision 6 extends across the executor boundary.
- Code read (2026-07-06): `feature_engineering/engine.py` (row-wise transform), `feature_engineering/feature_store.py` (Redis ring buffer), `extensions/builtins.py` (`statistical` via feature store, `rolling_stat` stub), `spark_jobs/kafka_aggregations.py` (existing watermarked state store).
