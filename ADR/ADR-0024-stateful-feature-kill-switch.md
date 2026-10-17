<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0024 — Stateful-feature kill-switch

- **Status:** Accepted
- **Date:** 2026-07-06; changed 2026-10-17
- **Parent:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (extends the transform contract)
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (shared schema), [ADR-0016](ADR-0016-observability-and-monitoring-integration.md) (degraded serving is visible), [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (the aggregate read guarded)

## Context

Stateful transforms read the database's materialized aggregates; when that read degrades, the per-call
fallback still issues every failing call and gives operators no control. Removing a feature from a
configuration changes the vector a bound model expects. The engine holds no transform-type knowledge, so
it cannot know which transforms are stateful.

## Decision

1. **Transforms declare their class at registration.** An optional stateful flag, default false, is an
   additive extension-API change (ADR-0010 dec 3), and every statistic of a stateful type is stateful.
2. **One global, live switch in the database's shared schema.** An operator flips it, the serving path
   reads it with a short cache, and it is platform data, read schema-qualified (ADR-0003).
3. **Null-in-slot.** When off, each stateful slot takes its transform's neutral value (zero unless
   declared) without the transform being called, so vector shape is unchanged.
4. **The switch is checked before the substrate call.** A disabled class issues no aggregate reads.
5. **Degraded serving is observable.** Neutralizations are logged and metered, and the inference response
   names the neutralized features.
6. **Global and manual: the switch is platform-wide and changes only when an operator flips it.**
7. **The switch fails open, on last-known-good.** A failed read keeps the last read value and a never-read
   switch is enabled, because the incident it serves is an overloaded database, not an unreachable one;
   failing closed would be auto-trip without hysteresis.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Per-feature toggles in each configuration | Changes vector shape; the incident is class-wide |
| Neutralize after calling the transform | Still loads the degraded store |
| Flag in an environment setting | Not live without a restart |
| Flag in the cache store | Absent when that store is |
| Flag in a third store | New serving dependency for a total outage already served neutral |
| The per-call fallback alone | No relief, no control, no visibility |

## Consequences

**Positive**
- A degraded aggregate read lowers feature quality instead of stopping inference, and stops being loaded.
- Every future stateful transform is covered by its tag; flipping needs no retrain.

**Negative**
- While off, models serve on partially neutral input.
- In a total database outage the switch cannot help; timeout and fallback bound the damage.
- Mean-valued slots at zero are further out of distribution than a zero deviation.

## Deferred

| Open question | Settled by |
|---|---|
| Per-tenant scope | The first incident confined to one tenant |
| Auto-trip with hysteresis | Repeated manual flips on the same signal |
| Neutral value per statistic | Evidence that mean-valued slots at zero distort scores |
| Authenticated operator endpoint | An access-control decision for writes on the inference service |

## References

- [docs/operations/monitoring.md](../docs/operations/monitoring.md): runbook and metric.
- `services/ml_service/api/feature_engineering/`: `kill_switch.py` (decisions 2, 7), `engine.py` (3–5)
