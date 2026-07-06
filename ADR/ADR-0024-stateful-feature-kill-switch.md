<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0024 — Stateful-feature kill-switch (null-in-slot, live via config)

- **ID:** ADR-0024
- **Status:** Proposed (build-ready — not trigger-gated, unlike ADR-0023)
- **Date:** 2026-07-06
- **Project:** IndustryFlow
- **Parent:** ADR-0010 (extension/plugin mechanism — extends the transform contract with a capability tag)
- **Companions:** [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (the safety valve for its Redis-demotion/operational-burden trigger), [ADR-0016](ADR-0016-observability-and-monitoring-integration.md) (surfacing degraded serving).

## Context and problem

Some feature transforms are **stateful**: they depend on the Redis FeatureStore (`feature_engineering/feature_store.py`). Today only `statistical` (`deviation_from_run_mean`) does — it calls `ctx.feature_store.compute_rolling_mean(...)` — and `rolling_stat` is a stub in the same class. The stateless transforms (`identity`, `polynomial`, `interaction`, `deviation`) are pure functions of the current snapshot and touch no external state.

When the stateful substrate degrades, the serving path has no operator-controlled graceful mode:

- `statistical` already returns the raw value on a per-call exception (`extensions/builtins.py`), but that is **reactive** (one failed call at a time), gives no operator control, and — critically — still **issues the failing feature-store call every time**, so it does not relieve a degraded Redis.
- There is no way to switch a class of features **off** live. Removing a feature from a config to disable it changes the feature vector's length and order, which **breaks every model bound to that config** — a trained model expects a fixed-length, fixed-order vector.
- The engine (`feature_engineering/engine.py`) deliberately holds **no** transform-type knowledge (ADR-0010): it dispatches purely by `type`. So the engine cannot itself know which transforms are stateful — the class must be declared elsewhere.

What is wanted is a **class-level kill-switch**: one live operator control that neutralizes the stateful class, keeps models serving on a defined (neutral) input, and stops calling the degraded dependency — without a redeploy and without changing the vector shape.

This is **not** the substrate migration (ADR-0023). It protects the *current* Redis path and needs no streaming layer; it is build-ready now.

## Decision drivers

- **Graceful degradation over hard failure.** A degraded substrate should down-grade feature quality, not take inference down.
- **Relieve the degraded dependency.** The switch must *cut* the calls to the failing store, not merely null their result after making them.
- **Preserve the model contract.** Turning features off must not change vector length or order; bound models keep serving.
- **Keep the engine transform-agnostic.** The engine must not hardcode which types are stateful (ADR-0010); the class is declared at registration.
- **Live control.** Flippable without a redeploy or restart.
- **Independent of ADR-0023.** Valuable even if the substrate is never migrated — it hardens the path that exists.

## Decision

1. **Transforms declare their class at registration.** The ADR-0010 transform contract gains an optional `stateful` capability flag: `register_transform(name, *, stateful=False)`. The registry carries the tag; `get_transform`'s callers can ask whether a type is stateful. Stateless built-ins are unchanged (default `False`); `statistical` and `rolling_stat` register `stateful=True`. This is an **additive** contract change — an optional keyword with a back-compatible default — so it is a **minor** extension-API bump (`EXTENSION_API_VERSION` 0.1.0 → 0.2.0 per ADR-0010 dec 3), never a breaking major, and existing/domain transforms that don't pass it keep working.

2. **A single, global, live kill-switch.** One operational flag — *stateful features enabled: yes/no* — is read live on the serving path and flipped by an operator. It is **global** (platform-wide), matching the failure it guards (a shared substrate is degraded for everyone), and **DB-backed with a short read cache**. It is deliberately **not** stored in Redis (the switch would be dead exactly when the substrate it guards is down) and **not** an env var (not live without a restart).

3. **Null-in-slot semantics.** When the switch is off, the engine fills each stateful transform's slot with the **neutral value** (`0.0` by default, reusing the engine's existing missing-feature fallback) **without calling the transform**. Vector length and order are unchanged, so every bound model keeps serving — on partially-neutralized input. A transform may declare a different neutral value if `0.0` is not neutral for it (optional; default `0.0`).

4. **Short-circuit before the substrate call.** The switch check sits in `engine._apply_transformation` **ahead of** `await fn(...)`, so a killed class issues **zero** feature-store calls. Relieving the degraded dependency — not just protecting the model — is the point; nulling *after* the call would protect the model but keep hammering the store, which is useless in the incident this is built for.

5. **Degraded serving is observable.** Flipping the switch is not silent: the switch state and per-inference neutralization are logged and metered, and the inference response carries a marker that its score was computed with the stateful class neutralized (ADR-0016 surface). Trading a loud failure (store errors) for a silent one (confidently-wrong scores) is not acceptable.

6. **Global-first, manual-first.** The first cut is one global, operator-flipped switch. Per-tenant scoping and **auto-trip** on the feature-store health-check are additive follow-ons, explicitly deferred — auto-trip needs hysteresis to avoid flapping and is a separate decision.

## Alternatives considered

**A. Per-feature toggles inside each config.** *Rejected as the first cut:* turning an individual feature off changes vector shape and breaks the bound model, and the operational need is a *class* valve (a degraded substrate hits every stateful feature at once), not per-feature editing. Per-tenant / per-feature control is an additive later, on top of the null-in-slot mechanism this ADR establishes.

**B. Null the result *after* calling the transform.** *Rejected:* protects the model but still makes every failing call, so it does not relieve the degraded substrate — it fails the primary driver.

**C. Flag in an env var or in Redis.** *Rejected:* an env var is not live without a restart; a Redis-stored flag is circular — dead precisely when Redis is the problem.

**D. Rely on the existing per-call exception fallback in `statistical`.** *Rejected:* it is reactive, not operator-controlled; it still issues the failing call every time (no relief); and it does not generalize to other stateful transforms or expose a single visible switch.

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

## Deferred decisions

- **Per-tenant scoping** of the switch (global-only in the first cut).
- **Auto-trip** on the feature-store health-check, with hysteresis / flap protection.
- **Per-transform neutral values** beyond the `0.0` default.
- **Operator surface**: the exact endpoint, RBAC, and the config table shape for the live flag.

## References

- ADR-0010 — extension/plugin mechanism; the transform registry and contract this extends with a `stateful` capability tag (minor API bump, dec 3).
- ADR-0023 — stream-materialized feature engineering; companion — this switch is the operational safety valve behind its Redis-demotion / operational-burden trigger (dec 7–8), available independently and earlier.
- ADR-0016 — observability & monitoring integration; the surface on which degraded serving (decision 5) is made visible.
- Code read (2026-07-06): `feature_engineering/engine.py` (transform-agnostic dispatch; the `0.0` fallback that prefigures null-in-slot; the point where the switch check belongs), `extensions/__init__.py` (`register_transform` signature to extend), `extensions/builtins.py` (`statistical` touches `ctx.feature_store`; `rolling_stat` stub), `feature_engineering/feature_store.py` (the substrate the switch relieves).
