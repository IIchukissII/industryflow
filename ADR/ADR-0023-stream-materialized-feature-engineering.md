<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0023 — Stream-materialized feature engineering (one windowing substrate, the feature table as the seam) (rev 4)

- **ID:** ADR-0023
- **Status:** Accepted
- **Date:** 2026-07-06 (rev 1: 2026-07-06; rev 2: 2026-07-12; rev 3: 2026-10-13; rev 4: 2026-10-13)
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (schema-per-tenant routing), [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (Kafka delivery semantics), [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (Spark windowing & idempotent writes), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extension/plugin mechanism).
- **Supersedes:** ADR-0023 rev 0 (2026-07-06, Proposed — direction only, no code until a trigger); rev 1 (2026-07-06 — first realization: the baseline is served from the existing aggregates and the serving side's windowing store is removed); rev 2 (2026-07-12 — the skew trigger fired; offline training re-derives the same window); rev 3 (2026-10-13 — makes decisions 7 and 8 and the deferred list agree with rev 1); rev 4 (2026-10-13 — rev 2 bound training and serving by granularity alone and described serving as reading the last closed window; rev 4 binds them by granularity and lateness to the newest *written* window, decision 9, and records that the alert worker's snapshot keys carry the tenant, decision 7)

## Context and problem

The platform computes engineered features in two places that do not know about each other, and for windowed (stateful) features they compute overlapping things through different substrates.

- The **feature engine** (`services/ml_service/api/feature_engineering/engine.py`) is config-driven and row-wise: `transform()` takes a single `sensor_data` snapshot and returns an ordered feature vector, dispatching each transform type through the extension registry (ADR-0010). It holds no transform knowledge itself. Stateless transforms — `identity`, `polynomial`, `interaction`, `deviation` (`extensions/builtins.py`, a sibling package of `feature_engineering/`) — are pure functions of the current snapshot.
- The **stateful** transforms are different. `statistical` (`deviation_from_run_mean`) does not compute a window; it does a read-side lookup against the **FeatureStore** (`feature_engineering/feature_store.py`), which is a **Redis ring buffer** — the last ~100 readings per `equipment:sensor`, TTL 1h — with `compute_rolling_mean(window=50)`. `rolling_stat` is an unimplemented placeholder.
- Meanwhile the **Spark aggregation job** (`services/spark_jobs/kafka_aggregations.py`) already maintains a watermarked streaming **state store** to compute 1 min / 5 min / 1 hour roll-ups into TimescaleDB (ADR-0006).

So there are **two independent windowed-state mechanisms** — Spark's state store and the Redis FeatureStore — computing the same *kind* of thing (a rolling statistic over a window) through unrelated substrates. Two further problems follow from the split. First, **train/serve skew**: training reads history one way and inference reads the Redis ring buffer another, with no guarantee the two agree. Second, `rolling_stat` is unimplemented precisely because there was no stream substrate to compute it against; the Redis buffer is a bounded ring, not a windowing engine.

There is no *bug* here today — `statistical` works off Redis and the aggregation job works off Spark. This ADR does not order a rewrite. It records the **direction** for stateful feature computation so the decision is not re-derived from scratch when the first real windowed feature is built, and states the sharp trigger that should move it from proposed to implemented.

**Revision 1 (2026-07-06).** The first realization: the rolling-mean baseline the `statistical` transform needs is served from the aggregates the Spark job already materializes, and the serving side's own windowing store is removed rather than kept beside it. A dedicated feature table stays deferred (decision 2).

**Revision 2 (2026-07-12).** Decision 8's train/serve-skew trigger fired: a reference model was trained on a whole-run mean while inference served a windowed one. Rev 2 settles what "training consumes the aggregate" means for offline data and binds both sides to one window (decision 9).

**Revision 3 (2026-10-13).** Rev 1 changed decisions 2 and 7 but left decision 8 and the deferred list describing the system before it: decision 8 still said that until a trigger fires there are no code changes and the stateful feature keeps its old windowing store, and a deferred item still asked whether that store would be removed or demoted "when the first feature actually moves". Both were settled by rev 1 — the baseline class moved, and the store was removed from serving. Rev 3 restates decision 8 and closes the deferred item so the record agrees with itself. It also states what decision 7 already implied and the code confirms: the alerting worker's separate latest-value store is a snapshot join, not a windowing computation, and remains outside this decision.

**Revision 4 (2026-10-13).** Decision 9 bound the two sides of the skew fix by granularity and described the served value as the most recent window that closed before the reading. Review found that is not the window serving can read. A window is *written* only once the watermark has passed its end by the allowed lateness (ADR-0006 dec 1-2), so at serve time the newest window available lags the newest *closed* one by the lateness — and the offline re-derivation, which took the window immediately before the reading, described a row by a window serving would not yet have had. Granularity alone did not determine the quantity; granularity and lateness together do. Rev 4 restates decision 9 accordingly. Separately, the driver that motivated structural tenant isolation quoted a cache key with no tenant in it, and decision 7 left the alerting worker's latest-value store keyed the same way; that store's keys now carry the tenant, and rev 4 says so rather than leaving the record quoting a key shape the platform no longer uses.

## Decision drivers

- **One windowing substrate, not two.** The platform already runs a watermarked Spark state store (ADR-0006). A second windowing engine (the Redis ring buffer) computing overlapping rolling statistics is duplicated state, not added capability. The stateful feature path should reuse the substrate that already exists rather than maintain a parallel one.
- **Kill train/serve skew by construction.** If features are computed once and materialized, training and inference read the *same* feature rows. Skew is removed by there being a single computation path, not by keeping two paths in sync.
- **Tenant isolation should be structural, not incidental.** When this record was written, the serving side's windowing cache — and the alerting worker's latest-value store, which used the same key shape — keyed entries by equipment and sensor with no tenant in the key; tenant isolation rested implicitly on equipment identifiers being non-colliding UUIDs. A Spark group key makes the tenant boundary explicit. *(rev 4: the serving cache is gone (decision 7) and the alerting worker's keys now carry the tenant.)*
- **Decouple production from consumption.** Whoever computes features and whoever reads them (training, inference) should meet at a contract, not a shared code path — the same "decoupled by default, bound by contract" discipline applied elsewhere.
- **Record the direction lazily; build on a trigger.** Nothing here needs code today. The value now is a written decision so the design is not lost; the code lands when a real windowed feature demands it.

## Decision

1. **Stateful, windowed features compute in Spark's existing state store — not a second Redis window.** When real rolling/lag/windowed features are implemented, they are computed in the watermarked streaming layer established by ADR-0006, reusing that state store rather than introducing or extending an independent windowing mechanism.

2. **A materialized Spark table is the seam; the first realization reuses the existing aggregates.** Spark *produces* the windowed value into a per-tenant table that inference (ml_service) and training (notebooks) *consume*, decoupled and bound only by the table contract (idempotent on the natural key, ADR-0006 dec 3). **rev 1:** the first realization adds *no new table* — the rolling-mean/std baseline the `statistical` feature needs is already materialized by the aggregation job as `sensor_aggregations_*.avg_value` / `stddev_value` (ADR-0006), so that aggregate table **is** the seam for the baseline class. Inference reads it tenant-scoped: the transform's `TransformContext` is reshaped — the Redis `feature_store` handle becomes a `baseline_provider` that reads the aggregates, plus a `company_id` for tenant-schema routing (an ADR-0010 minor at extension-API 0.x: no domain transform depends on the old handle). Standing up a *dedicated* feature table (the original framing) is **deferred** to engineered features the aggregates cannot provide — building one now to hold a value the aggregates already carry would be the duplicated state driver #1 rejects.

3. **Stateless, row-wise transforms stay substrate-neutral.** `identity`, `polynomial`, `interaction`, `deviation` and their kind are pure functions of a snapshot and do not require Spark; they may be computed wherever is cheapest (ingestion path, stream, or on demand). This ADR does not force them into the stream — only the stateful class benefits from the move.

4. **Tenant isolation is the stream partition key.** Windowed features group by `(company_id, equipment_id, sensor, window)`, so per-tenant state is partitioned structurally by the state store and written to the tenant schema via the existing `company_id_to_schema` routing (ADR-0003). This replaces the current implicit-via-UUID keying of the Redis store with an explicit tenant partition — an isolation upgrade, not merely a port.

5. **Per-tenant feature configuration is routed live within the shared stream.** A mixed-tenant micro-batch applies each tenant's `transformations` set to that tenant's rows (`applyInPandas` keyed by tenant), with the per-tenant config picked up without a stream restart (config topic / broadcast state). A config edit changes future output without redeploying the job. This is the real work the move requires; isolation (decision 4) is free, config liveness is not.

6. **The ADR-0010 transform registry must hold across the Spark executor boundary.** Domain transform modules are shipped to executors (`--py-files`) and the registry is populated there, so `get_transform` resolves the same built-in and domain transforms inside Spark as it does inside ml_service. The ADR-0010 extension contract now spans the executor boundary, not just a single FastAPI process.

7. **The Redis FeatureStore stops being a windowing substrate.** With the baseline sourced from the Spark aggregates (decision 2), Redis ceases to be a second *computation* path: `compute_rolling_mean` becomes a read of the materialized aggregate. **rev 1:** ml_service's Redis FeatureStore is **removed outright** — its only use was that rolling-mean read. The **alert worker's** separate Redis ring (`services/alert_service/worker`) is a *latest-value snapshot join* — assembling a full multi-sensor reading from per-sensor arrivals before inference — **not** a windowing computation; it is out of this decision's scope and untouched. So Redis is not deleted platform-wide; it stops being a windowing/feature-computation substrate, which is the claim this ADR makes. A cache in front of the aggregate read is a later option, not part of rev 1.

   > **rev 4 (2026-10-13): the alerting worker's snapshot keys are tenant-scoped.** That store is still a snapshot join and still outside this decision's windowing scope, but driver 3's reasoning applies to it as much as to a feature store: its keys carry the tenant as well as the equipment and sensor, so isolation no longer rests on identifiers happening not to collide.

8. **Trigger and scope *(rev 3)*.** The direction was recorded ahead of the code, with three triggers: the first real windowed feature, observed train/serve skew, or the serving side's windowing store becoming an operational burden. **For the baseline class the direction is now realised** (rev 1): the windowed baseline is read from the materialized aggregates, and the serving side holds no windowing store. The skew trigger then fired (rev 2), and decision 9 is what it bought. **What remains trigger-gated** is the rest of this record: computing new windowed features in the stream (decisions 4–6), the dedicated feature table (decision 2), and the unimplemented rolling-statistic transform — each lands when a real feature needs it, not before.

   > *Rev 0 read:* "Until a trigger fires there are no code changes: `rolling_stat` stays a stub and `statistical` keeps its current Redis path." The first half still holds for the rolling statistic; the second stopped being true at rev 1.

9. **Offline training data re-derives the window; it does not invent one. (rev 2)** Decision 2 says training *consumes* the materialized aggregate, which was written for the live-DB case. Reference models train on a static CSV that cannot issue that read, so "consume" is made concrete: training **re-derives the same closed tumbling window, at the same granularity, causally** — a row is described by the most recent window that closed *before* its own, never by its own (still open at serve time) and never by the future. A whole-run or otherwise non-causal mean is **not** an acceptable training equivalent: it is not merely less accurate, it is a quantity inference cannot produce at any window size, so a model trained on it is fed a feature it has never seen. Concretely: `groupby(run).transform('mean')` is prohibited for this feature class; a trailing rolling mean is acceptable **only** where its width equals the configured granularity and it requires a full window.

   The binding between the two computations is **`granularity`, carried in the feature config** and read by both sides — the serving transform routes it to `sensor_aggregations_<granularity>`, the offline re-derivation windows to the same width. Windowing differently now requires editing the config, where review can see it, rather than being the silent default it was. Correspondingly, `stat_type` is **required** and names an honest statistic (`deviation_from_window_mean`, `window_mean`, `window_std`); the pre-rev-2 name `deviation_from_run_mean` is a deprecated alias, since there is no "run" at serve time, and its old behaviour of being the *default* is removed — a feature that named no statistic silently received a deviation, which is how `_run_mean` and `_run_std` features came to be served a deviation. Training and serving may run different code, but they may not compute different quantities, and a test asserts they agree on the same data rather than merely being intended to.

   > **rev 4 (2026-10-13): serving reads the newest *written* window given lateness, and training uses that same window; the binding is granularity plus lateness.** At serve time the baseline is the newest window the aggregation has actually written, and a window is written only once the watermark has passed its end by the allowed lateness. So the window that describes a reading is the newest one written by that reading's instant — not the one that closed immediately before it, which serving cannot yet see. Offline training re-derives **that** window: the same width, the same lateness, the same rule for which window was the newest written. The binding between the two sides is therefore **granularity and allowed lateness together**, which are a feature contract fixed with the code (ADR-0006 dec 7 rev 2) rather than deployment settings; each side states them, and the agreement test holds them equal. The causal rule above is unchanged — never the reading's own window, never the future — and is now exact about which past.

## Alternatives considered

**A. Keep the two windowing substrates (status quo).** *Rejected:* Spark's state store and the Redis ring buffer compute overlapping rolling statistics through unrelated mechanisms — duplicated state and a standing train/serve-skew risk. Keeping both is maintaining two answers to one question. (Accepted *temporarily* only until decision 8's trigger, since nothing is broken today.)

**B. Compute rolling features in ml_service Python over fetched history.** *Rejected:* it recreates a windowing engine in the serving layer, over a DB or Redis read, in parallel to the Spark state store that already exists — the same duplication as A, relocated. The stateful class belongs in the substrate already doing windowing.

**C. Fold model *inference* into Spark at the same time.** *Rejected here as out of scope:* inference carries a per-tenant model artifact and a blast-radius coupling (one tenant's slow/failing model stalls the shared batch) that feature transforms do not — a transform is uniform code with nothing to load and nothing to fail slowly. Inference-in-stream is a separate decision with a different cost profile and is not bundled into this one.

**D. Materialize into one shared feature table keyed by tenant column, not per-tenant schemas.** *Rejected:* it breaks the schema-per-tenant isolation the platform is built on (ADR-0003) and the tenant-reader role model. Features follow the same per-schema routing as measurements and aggregates.

**E. Bind training and serving by granularity alone, and accept the lateness gap *(rev 4)*.** *Rejected:* the gap is not noise. A model trained on the window immediately preceding each reading is served a window one lateness older, which is exactly the train/serve skew decision 9 exists to remove, reintroduced at the edge of the window.

## Consequences

### Positive

- One windowing substrate: the Redis ring buffer stops being a parallel windowing engine, and stateful features reuse the Spark state store the platform already runs.
- Train/serve skew is removed by construction — training and inference read the same materialized feature rows. **rev 2:** for *offline* training data that construction does not hold on its own — a static CSV cannot read the table, so it re-derives (dec 9) and there are two implementations of one quantity. What keeps them honest is a single shared definition plus a test that asserts the offline column and the live serving transform return the same number for the same data. "Removed by construction" is true of the live path; on the offline path it is true only because something checks.
- `rolling_stat` becomes implementable, because there is now a stream substrate to compute it against.
- Tenant isolation for features moves from implicit (non-colliding UUID) to explicit (stream partition key + tenant schema).
- Feature production and consumption are decoupled behind a table contract, consistent with the platform's seam discipline.

### Negative

- Live per-tenant config routing in a shared stream (decision 5) is real work: picking up a tenant's edited `feature_config` without restarting the job needs a config-topic/broadcast-state mechanism that does not exist yet.
- The ADR-0010 registry must be shipped to and populated on executors (decision 6); the extension contract now spans the Spark boundary and must be tested there, not only in-process.
- Materialize-on-close raises feature latency versus an on-demand Python compute: a windowed feature appears when its window closes (plus watermark lag), not instantly on request — the same trade ADR-0006 dec 2 already makes for aggregates.
- A migration exists for any feature currently served from Redis if/when it moves; the demotion of the FeatureStore (decision 7) is a change to a live read path.
- **rev 2:** the reference models **must be retrained**. Their `_deviation` features now hold a different (and finally reproducible) quantity, and `_run_mean`/`_run_std` are replaced by `_window_mean`/`_window_std`. Existing model artifacts trained on the old columns are not merely stale, they were fitted on a feature inference never produced; their reported metrics are not evidence about the deployed system. Any downstream evaluation is invalidated with them.
- **rev 2:** the offline re-derivation is a second implementation of the serving computation, so it is a standing skew risk of its own — the mitigation is that it is one function and one test, not that it cannot drift.

## Deferred decisions

- **Config-reload mechanism.** Config topic vs broadcast state vs periodic reload for decision 5, and the freshness guarantee a config edit gets, are implementation choices left to the build.
- **Feature table schema and retention.** Column shape, natural key, and retention/compression for the materialized feature table are a data-model task (companion to the ADR-0006 aggregate tables), not fixed here.
- ~~**Redis: removed or demoted.**~~ *Resolved in rev 1 (decision 7), recorded in rev 3:* removed from the serving side outright, with no cache in front of the aggregate read; a cache remains a later option if the read proves costly. The alerting worker's latest-value snapshot store is a different thing and out of scope.
- ~~**Feature window horizons.**~~ *Resolved by ADR-0006 rev 2 (decision 7) and rev 4 here (decision 9):* window width and allowed lateness are a feature contract fixed with the code, shared by the stream, serving and offline training; their values are owned by that code.

## References

- ADR-0001 — framing; the generic-platform scope under which feature engineering is a platform capability and domain transforms are extensions.
- ADR-0003 — tenant-to-schema resolution; the `company_id_to_schema` routing and schema-per-tenant isolation decisions 4 and D rely on.
- ADR-0005 — Kafka delivery semantics; at-least-once, which the materialized-write idempotency assumes.
- ADR-0006 — Spark windowing & idempotent writes; the watermarked state store and idempotent-upsert discipline this decision reuses for features.
- ADR-0010 — extension/plugin mechanism; the transform registry whose contract decision 6 extends across the executor boundary.
- Code read (2026-07-06): `feature_engineering/engine.py` (row-wise transform), `feature_engineering/feature_store.py` (Redis ring buffer), `extensions/builtins.py` (`statistical` via feature store, `rolling_stat` stub), `spark_jobs/kafka_aggregations.py` (existing watermarked state store).
