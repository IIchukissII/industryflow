<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0024 — Stateful-feature kill-switch (null-in-slot, live via config)

- **ID:** ADR-0024
- **Status:** Accepted (rev 1 — implemented. The guarded substrate is now the Postgres aggregate tables, not Redis: ADR-0023 rev 1 removed ml_service's Redis feature store, which invalidated this ADR's original context and forced dec 2's circularity question to be answered properly, in dec 7)
- **Date:** 2026-07-06 (rev 1: 2026-07-12)
- **Project:** IndustryFlow
- **Parent:** ADR-0010 (extension/plugin mechanism — extends the transform contract with a capability tag)
- **Companions:** [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (the safety valve for its Redis-demotion/operational-burden trigger), [ADR-0016](ADR-0016-observability-and-monitoring-integration.md) (surfacing degraded serving).

## Context and problem

> **rev 1 (2026-07-12):** the substrate named throughout this section is **out of date**, and the correction matters enough to state before the original text rather than after it. When this ADR was written the stateful class depended on the **Redis FeatureStore**. ADR-0023 rev 1 then **removed that store outright**: `statistical` now reads the Spark-materialized aggregates from **Postgres** (`sensor_aggregations_<granularity>.avg_value` / `stddev_value`) through `AggregateBaselineProvider`, with a short in-process TTL cache and a per-query timeout, and it degrades to the **neutral `0.0`** on error — not to the raw sensor value this section describes. ADR-0023 rev 2 added `window_mean` and `window_std`, which read the same aggregate row and are stateful by the same definition.
>
> The problem this ADR solves is unchanged: it is a property of the *class*, not of which store backs it. What the substrate swap does change is that the switch's own backing store is now the very database it guards — see **dec 7**, which is the decision this ADR previously did not have to make.

Some feature transforms are **stateful**: they depend on an external store to answer, rather than being a pure function of the current reading. Today `statistical` does (it reads a windowed baseline; before ADR-0023 rev 1 from Redis, now from the Postgres aggregate tables) and `rolling_stat` is a stub in the same class. The stateless transforms (`identity`, `polynomial`, `interaction`, `deviation`) are pure functions of the current snapshot and touch no external state.

When the stateful substrate degrades, the serving path has no operator-controlled graceful mode:

- The transform already falls back on a per-call exception, but that is **reactive** (one failed call at a time), gives no operator control, and — critically — still **issues the failing call every time**, so it does not relieve the degraded store.
- There is no way to switch a class of features **off** live. Removing a feature from a config to disable it changes the feature vector's length and order, which **breaks every model bound to that config** — a trained model expects a fixed-length, fixed-order vector.
- The engine (`feature_engineering/engine.py`) deliberately holds **no** transform-type knowledge (ADR-0010): it dispatches purely by `type`. So the engine cannot itself know which transforms are stateful — the class must be declared elsewhere.

What is wanted is a **class-level kill-switch**: one live operator control that neutralizes the stateful class, keeps models serving on a defined (neutral) input, and stops calling the degraded dependency — without a redeploy and without changing the vector shape.

This is **not** the substrate migration (ADR-0023). It hardens the path that exists and needs no streaming layer.

## Decision drivers

- **Graceful degradation over hard failure.** A degraded substrate should down-grade feature quality, not take inference down.
- **Relieve the degraded dependency.** The switch must *cut* the calls to the failing store, not merely null their result after making them.
- **Preserve the model contract.** Turning features off must not change vector length or order; bound models keep serving.
- **Keep the engine transform-agnostic.** The engine must not hardcode which types are stateful (ADR-0010); the class is declared at registration.
- **Live control.** Flippable without a redeploy or restart.
- **Independent of ADR-0023.** Valuable even if the substrate is never migrated — it hardens the path that exists.

## Decision

1. **Transforms declare their class at registration.** The ADR-0010 transform contract gains an optional `stateful` capability flag: `register_transform(name, *, stateful=False)`. The registry carries the tag; `get_transform`'s callers can ask whether a type is stateful. Stateless built-ins are unchanged (default `False`); `statistical` and `rolling_stat` register `stateful=True`. This is an **additive** contract change — an optional keyword with a back-compatible default — so it is a **minor** extension-API bump (`EXTENSION_API_VERSION` 0.1.0 → 0.2.0 per ADR-0010 dec 3), never a breaking major, and existing/domain transforms that don't pass it keep working. **rev 1:** the stateful class is a property of the *transform type*, not of the `stat_type` inside it — so `statistical` is stateful for **all** of its statistics (`deviation_from_window_mean`, `window_mean`, `window_std`; ADR-0023 rev 2), since each reads the same aggregate row. The bump lands at 0.2.0 as written: ADR-0023 rev 1 reshaped `TransformContext` but never bumped the published version, which stood at 0.1.0.

2. **A single, global, live kill-switch.** One operational flag — *stateful features enabled: yes/no* — is read live on the serving path and flipped by an operator. It is **global** (platform-wide), matching the failure it guards (a shared substrate is degraded for everyone), and **DB-backed with a short read cache**. It is deliberately **not** stored in Redis (the switch would be dead exactly when the substrate it guards is down) and **not** an env var (not live without a restart). **rev 1:** the flag lives in **`public.platform_config`** — the shared schema, never a tenant one, because a platform-global flag is not tenant data (ADR-0003). It is read fully qualified as `public.platform_config` precisely because the inference path may hold a tenant `search_path`, where a bare table name would resolve into the tenant schema and fail.

3. **Null-in-slot semantics.** When the switch is off, the engine fills each stateful transform's slot with the **neutral value** (`0.0` by default, reusing the engine's existing missing-feature fallback) **without calling the transform**. Vector length and order are unchanged, so every bound model keeps serving — on partially-neutralized input. A transform may declare a different neutral value if `0.0` is not neutral for it (optional; default `0.0`).

4. **Short-circuit before the substrate call.** The switch check sits in `engine._apply_transformation` **ahead of** `await fn(...)`, so a killed class issues **zero** feature-store calls. Relieving the degraded dependency — not just protecting the model — is the point; nulling *after* the call would protect the model but keep hammering the store, which is useless in the incident this is built for.

5. **Degraded serving is observable.** Flipping the switch is not silent: the switch state and per-inference neutralization are logged and metered, and the inference response carries a marker that its score was computed with the stateful class neutralized (ADR-0016 surface). Trading a loud failure (store errors) for a silent one (confidently-wrong scores) is not acceptable.

6. **Global-first, manual-first.** The first cut is one global, operator-flipped switch. Per-tenant scoping and **auto-trip** on the feature-store health-check are additive follow-ons, explicitly deferred — auto-trip needs hysteresis to avoid flapping and is a separate decision.

7. **The switch fails open, on last-known-good. (rev 1)** Dec 2 rejected a Redis-backed switch as circular — dead exactly when the substrate it guards is down. After ADR-0023 rev 1 the guarded substrate **is the database**, so a DB-backed switch inherits the very circularity dec 2 was written to avoid. It is kept DB-backed anyway, because the two failure modes are not the same shape, and the resolution is to say plainly which one this switch is for:

   - **The incident this switch exists for is a database that is alive but overloaded** — degraded, in significant part, *by the N-stateful-features × every-inference baseline queries the switch cuts*. In that state a single tiny cached config row still reads fine while the hypertable queries time out. The switch works precisely where it is needed.
   - **A totally unreachable database is not this switch's incident.** There, the switch read fails too. It then reads as **enabled** (see below), the baseline reads fail, and the per-call fallback returns the neutral `0.0` — the same input the switch would have produced. Inference keeps serving; nothing is worse than it was before the switch existed. The switch cannot *relieve* a database that is already answering nothing, and does not pretend to.

   **On a failed read the switch holds its last successfully-read value; if it has never read one, it is `enabled`.** Failing *closed* (assume killed when unreadable) is auto-trip wearing a disguise: a transient blip — a pool exhaustion, a restart, a network hiccup — would silently neutralize a feature class with no operator deciding anything, and it would flap. Dec 6 defers auto-trip precisely because it needs hysteresis and is its own decision; a fail-closed read rule would smuggle it in through the back door. So the switch only ever kills features because a human turned it off.

## Alternatives considered

**A. Per-feature toggles inside each config.** *Rejected as the first cut:* turning an individual feature off changes vector shape and breaks the bound model, and the operational need is a *class* valve (a degraded substrate hits every stateful feature at once), not per-feature editing. Per-tenant / per-feature control is an additive later, on top of the null-in-slot mechanism this ADR establishes.

**B. Null the result *after* calling the transform.** *Rejected:* protects the model but still makes every failing call, so it does not relieve the degraded substrate — it fails the primary driver.

**C. Flag in an env var or in Redis.** *Rejected:* an env var is not live without a restart; a Redis-stored flag is circular — dead precisely when Redis is the problem. **rev 1:** the circularity objection now lands on the DB-backed choice too, since the DB became the guarded substrate. It is answered, not dodged, in dec 7 — the switch is scoped to the overloaded-DB incident and fails open. A flag held *outside* every store it might guard (a third system) was not adopted: it would add an operational dependency to the serving path to protect against an incident (total DB outage) in which inference is already degraded to neutral anyway.

**D. Rely on the existing per-call exception fallback in `statistical`.** *Rejected:* it is reactive, not operator-controlled; it still issues the failing call every time (no relief); and it does not generalize to other stateful transforms or expose a single visible switch. **rev 1:** ADR-0023 rev 1 changed that fallback to return the neutral `0.0` rather than the raw value, so the fallback and the kill-switch now put the *same number* in the slot. That narrows the difference to relief, control, and visibility — which is the entire point of this ADR, so the rejection stands unchanged. It also means enabling the switch costs nothing in model input: it is strictly the same degradation, decided deliberately and made visible.

**E. Disable a feature by removing it from the config.** *Rejected:* the shape change breaks the model, it is not live, and it is not cheaply reversible.

## Consequences

### Positive

- A degraded stateful substrate degrades feature *quality* instead of taking inference down; models keep serving.
- The switch **cuts** calls to the failing store, relieving it (decision 4), rather than merely masking the result.
- The engine stays transform-agnostic; the capability tag lives with the transform (decision 1), and the mechanism generalizes to every future windowed/stateful transform for free.
- Ships now, in one service, with no streaming work — the operational pressure-valve that ADR-0023 dec 8 assumed, available before (and independent of) any substrate migration.
- Vector shape is preserved, so no model retrain or rebind is needed to flip the switch.

### Negative

- Null-in-slot means models serve on quietly-degraded input while the switch is off — mitigated but not eliminated by the observability requirement (decision 5); an operator who leaves it off silently lowers score quality.
- The ADR-0010 transform-registration signature gains a field. It is back-compatible (default `False`) and a minor API bump, but it is still a contract touch that transforms and domain plugins now live alongside.
- The neutral value is a modelling assumption: `0.0` is neutral for deviation-style features but may not be for every transform, which is why decision 3 allows a per-transform override.
- **rev 1:** the switch is powerless in a *total* DB outage — it reads as enabled (dec 7) and the baseline queries are attempted and fail. This is the residual of the circularity dec 2 tried to avoid, and it is accepted rather than solved: the per-call timeout and neutral fallback bound the damage, and inference still serves. An operator who watches the switch fail to help during a full outage should not "fix" it by inverting to fail-closed — that is the auto-trip dec 6 defers, and it would flap on every transient blip.
- **rev 1:** `statistical` is stateful for *all* its statistics, so the switch neutralizes `window_mean` and `window_std` too — a `window_mean` slot goes to `0.0`, which is *not* a plausible mean for a real sensor (unlike a deviation, for which `0.0` reads as "no deviation"). The vector stays the right shape and the model keeps serving, but for mean-valued slots the neutral is further out of distribution than it is for deviation-valued ones. Decision 3's per-transform neutral override does not help here, because the two statistics share one transform type; a per-`stat_type` neutral is the fix if this proves to matter, and is deferred rather than guessed at.

## Deferred decisions

- **Per-tenant scoping** of the switch (global-only in the first cut).
- **Auto-trip** on the feature-store health-check, with hysteresis / flap protection.
- **Per-transform neutral values** beyond the `0.0` default — and, per the negative above, **per-`stat_type`** neutrals, which the current per-type tag cannot express.
- **Operator surface**: the exact endpoint and RBAC for flipping the flag. **rev 1:** the config table shape is no longer deferred (`public.platform_config`, dec 2). The first cut is flipped with a SQL statement by an operator, documented in the runbook; an authenticated admin endpoint is the follow-on, and is deliberately not built here so that a new write surface is not opened on the inference service without an RBAC decision to go with it.

## References

- ADR-0010 — extension/plugin mechanism; the transform registry and contract this extends with a `stateful` capability tag (minor API bump, dec 3).
- ADR-0023 — stream-materialized feature engineering; companion — this switch is the operational safety valve behind its Redis-demotion / operational-burden trigger (dec 7–8), available independently and earlier.
- ADR-0016 — observability & monitoring integration; the surface on which degraded serving (decision 5) is made visible.
- ADR-0003 — tenant-to-schema resolution; why the global flag lives in the shared `public` schema and is read fully qualified (dec 2, rev 1).
- Code read (2026-07-06): `feature_engineering/engine.py` (transform-agnostic dispatch; the `0.0` fallback that prefigures null-in-slot; the point where the switch check belongs), `extensions/__init__.py` (`register_transform` signature to extend), `extensions/builtins.py` (`statistical` touches `ctx.feature_store`; `rolling_stat` stub), `feature_engineering/feature_store.py` (the substrate the switch relieves).
- Code read (rev 1, 2026-07-12): `feature_engineering/baseline_provider.py` (`AggregateBaselineProvider` — the Postgres aggregate read that *replaced* the Redis feature store, and the substrate this switch now guards; `feature_store.py` no longer exists), `extensions/builtins.py` (`statistical` with the ADR-0023 rev 2 stat types, all reading the same aggregate row).
