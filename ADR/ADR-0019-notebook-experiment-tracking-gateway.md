<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0019: Notebook experiment-tracking gateway and the tracking capability

- **ID:** ADR-0019
- **Status:** Proposed
- **Date:** 2026-06-28
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0013 (experiment-tracking & model-registry multi-tenancy — the shape this realizes), ADR-0015 (capability minting + the SQL proxy — the pattern this follows), ADR-0012 (per-session credential delivery), ADR-0011 (embedded notebooks)
- **Related:** ADR-0017 (database TLS — the gateway's MLflow backend connection), ADR-0014 (hub SSO — the verified identity the spawner mints from)

## Context and problem

ADR-0013 decided *that* experiment tracking and the model registry become multi-tenant by being mediated by a trusted, tenant-scoping **gateway**: notebooks present a per-session capability, the gateway holds the privileged store credentials and forces every operation into the caller's tenant, and the tracking server's broad credentials leave untrusted environments entirely. It deliberately left the **mechanism** open — exactly as ADR-0012 decided the *that* of credential delivery and ADR-0015 decided the *how* (opaque handles, audiences, the SQL proxy). This ADR is that follow-up for tracking: it makes ADR-0013's gateway concrete, the way ADR-0015 made ADR-0012's SQL proxy concrete.

The stores are already in place and single-tenant. MLflow runs as one server backed by a shared Postgres `mlflow` database and an `s3://mlflow` object-store bucket (MinIO); its database password and the object-store access keys are the broad credentials ADR-0013 decision 1 forbids in the kernel. The capability system (ADR-0015) already mints opaque, revocable, single-tenant, audience-bound handles for the **api** and **sql** planes; it does not yet have a tracking plane. And ADR-0011 decision 9 still **gates** notebook experiment tracking until this mechanism lands, because the only way to track today is to hand the kernel those shared credentials and separate tenants by naming convention — the exact shared-ambient-credential exposure the notebook design exists to forbid.

What is undecided, and what this ADR fixes: how the kernel reaches the gateway and authenticates; how the gateway scopes experiments, registered models, and artifacts to one tenant over the single shared store; and — the one genuinely open fork ADR-0013 deferred — how **artifact bytes** move without putting an object-store credential in the kernel.

## Decision drivers

- **The kernel holds an operation, not a credential (ADR-0012 dec 2, ADR-0013 dec 1).** A notebook needs to *log a run, read its runs, register a model* — never the tracking-store or object-store credential behind those operations.
- **Reuse the platform's one mediation pattern, don't invent a second (ADR-0013 dec 2).** The API gateway scopes a query and the SQL proxy scopes a connection; tracking should be scoped the same way — privileged principal in trusted code, scoped capability in the kernel.
- **Audiences must stay non-interchangeable (ADR-0015 dec 3).** A handle for tracking must not be exchangeable for SQL or API access, or vice versa.
- **One tenant dimension across all three stores (ADR-0013 dec 3).** Experiments/runs, registered models, and artifacts must carry the *same* boundary, with no path that scopes one and not the others.
- **Logical isolation first, physical later (ADR-0013 dec 5).** The boundary is the gateway over a single shared store; per-tenant servers/buckets are a deferred hardening step, not an up-front cost.
- **MLflow is unmodified and its native UI stays internal (ADR-0013 dec 4).** The scoping is the gateway's job; MLflow's own per-user auth is not tenant-aware and must not become the boundary.

## Decision

1. **Experiment tracking is reached only through a trusted tracking gateway; the kernel holds a per-session tracking capability, never the MLflow backend or object-store credentials (ADR-0013 dec 1-2 made concrete).** The kernel's MLflow client points `MLFLOW_TRACKING_URI` at the gateway and authenticates with a tracking-audience capability. The privileged MLflow backend connection and the object-store keys live only in the gateway. The shared tracking/object-store credentials are removed from every notebook environment.

2. **A third capability audience, `tracking`, is added and is audience-bound (extends ADR-0015 dec 3).** It has the same shape as `api`/`sql`: an opaque, high-entropy handle whose authority is a short-lived, revocable entry in the shared store recording the user, the one `company_id`, the audience, and an expiry (ADR-0015 dec 1-2). The gateway honours only `tracking` handles; possession of a `tracking` handle never yields SQL or API access, and neither yields tracking.

3. **The spawner is the sole minting authority and mints `tracking` for the authoring profile only (ADR-0015 dec 4, ADR-0011 dec 5).** Using the verified identity from hub entry (ADR-0014), the spawner mints the handle scoped to that one tenant and injects it (`INDUSTRYFLOW_TRACKING_*`) alongside the existing handles. The analytics (operator) profile authors no models and receives no tracking handle. The kernel cannot mint, refresh, or widen it.

4. **The kernel authenticates to the gateway with the capability as a bearer token (MLflow's `MLFLOW_TRACKING_TOKEN`).** The MLflow client already sends a bearer token to the tracking URI; the gateway reads it, resolves it in the store as a `tracking` handle (the lookup that grants is the one that revokes, ADR-0015 dec 1-2), and — holding the privileged MLflow principal — proxies the call. No MLflow auth plugin and no store credential ever sit in the kernel.

5. **The gateway forces every tracking, registry, and artifact operation into the caller's tenant namespace, and refuses any that would cross it (ADR-0013 dec 2-3 made concrete).** On the single shared MLflow store, the gateway namespaces experiments and registered models by tenant (a `tenant_<uuid>/` name prefix it adds on write and strips on read) and places artifacts under a per-tenant object-store prefix. It validates every request against the resolved tenant — rejecting any experiment name, run id, model name, or artifact path that resolves outside it — so the kernel sees plain names within its own tenant and cannot address another's. Runs, model versions, and their artifacts therefore carry one tenant dimension together (ADR-0013 dec 3).

6. **Artifacts are proxied through the gateway, not reached with object-store credentials (this resolves ADR-0013's deferred artifact sub-decision).** Artifact bytes flow kernel → gateway → object store under the tenant prefix, using MLflow's proxied-artifact serving; no object-store credential is placed in the kernel, and the tenant prefix is enforced at the proxy. Per-session, tenant-prefix-scoped, short-lived object-store credentials — the artifact analogue of the SQL capability — are retained as the deferred path if proxy throughput proves insufficient for large artifacts (alternative D; consistent with ADR-0013 dec 5's logical-first posture).

7. **The gateway is a small purpose-built component; MLflow is unmodified and its UI stays internal (ADR-0013 dec 4, ADR-0015 dec 6).** As with the SQL proxy, capability-resolution + tenant-namespace enforcement is exactly what MLflow's own per-user authorization does not do, so the gateway performs it and proxies to the unmodified server. The tracking server's native, non-tenant-aware web UI is not exposed to tenants; tenant-facing browsing of experiments and models is served later by the platform's own scoped API/UI (ADR-0013 dec 4, deferred).

8. **The store holds tracking handles, never tracking-store or object-store secrets (ADR-0015 dec 7).** The shared key-value store records only `tracking` handles and their bound facts; the privileged MLflow and object-store credentials stay in the gateway. A reader of the store sees revocable, single-tenant tracking handles and nothing worse.

9. **ADR-0011's gate is lifted for the authoring profile.** With the gateway mediating, ADR-0011 decision 9's hold on notebook experiment tracking is lifted: an authoring kernel may log runs, query its own runs, and register model versions within its tenant — and nothing outside it. This decision is the precondition that lift depends on.

## Alternatives considered

**A. Stand up a tracking server, backend, and bucket per tenant now.** *Rejected for now (inherits ADR-0013 dec 5 / alt A):* physical separation adds no boundary the gateway cannot already enforce, because the kernel never touches the store; it is kept as the deferred hardening path, not adopted up front.

**B. Expose MLflow directly to tenants using its own authorization plugin.** *Rejected (ADR-0013 alt B):* MLflow's open-source auth is per-user, not tenant-aware, and would put the store on the tenant-facing edge — the opposite of decision 1.

**C. Give the kernel the shared MLflow and object-store credentials and separate tenants by naming convention.** *Rejected (ADR-0013 alt C):* this is the status quo — broad ambient credentials inside user-authored code, cross-tenant by default — exactly what ADR-0011 dec 2 and ADR-0012 forbid, now over the tracking and artifact stores.

**D. Reach artifacts with per-session, tenant-prefix-scoped object-store credentials instead of proxying.** *Deferred, not rejected:* this is the artifact analogue of ADR-0015's SQL capability and is cleaner for very large artifacts (no proxy in the byte path), but it adds a credential-minting-and-scoping surface and an object-store IAM dependency. Decision 6 proxies first and keeps this as the throughput-driven follow-up.

**E. Reuse the SQL or API capability as the tracking credential.** *Rejected:* audiences are non-interchangeable by design (ADR-0015 dec 3); a single handle that reached data *and* tracking would widen the blast radius of one leaked handle beyond its plane.

**F. A thick client library that talks to MLflow directly and "scopes itself."** *Rejected:* the scoping code would run in the untrusted kernel — precisely the isolation-by-discipline-in-user-code that ADR-0011 forbids. The boundary must be trusted code (the gateway), not a library the kernel could bypass.

## Consequences

### Positive

- ADR-0011 decision 9's gate is lifted (decision 9): the authoring audience gets the experiment tracking and model registration it exists to do, within its tenant.
- The tracking and artifact stores stop being a cross-tenant exposure: their credentials leave untrusted environments entirely and every access is tenant-scoped (decisions 1, 5, 6).
- Tracking reuses the platform's one mediation pattern (API gateway, SQL proxy), so there is a single "trusted code holds the principal and scopes per tenant" model, not a new one (decisions 1, 4).
- Runs, model versions, and artifacts are isolated together under one tenant dimension; there is no partial-isolation path (decisions 5, 6).
- The single shared MLflow store is kept, avoiding per-tenant provisioning now while leaving physical separation as a clean later step (decisions 5-6, alt A/D).

### Negative

- The tenant boundary for tracking rests on the gateway being correct; a bug there is a cross-tenant leak — the same concentrated residual risk the platform accepts for its other trusted scoping paths (decisions 5, 7).
- A new trusted component must be built, hardened, and kept available on the path between notebooks and MLflow (decision 7).
- Proxying artifact bytes through the gateway puts it in the data path for potentially large model artifacts; throughput may force the deferred scoped-credential path (decision 6, alt D).
- MLflow's native UI, which data scientists expect, is not available to tenants; an equivalent tenant-scoped browsing experience is owed through the platform UI (decision 7, deferred).
- Existing single-tenant MLflow experiments, runs, models, and artifacts predate the tenant namespace and must be attributed or retired before the boundary is complete (deferred).

## Deferred decisions

- **Scoped-artifact-credential trigger.** The throughput conditions and mechanism for moving artifacts off gateway-proxying to per-session scoped object-store credentials (alternative D) are deferred until proxying is shown insufficient.
- **Tenant namespace literal format.** The exact experiment/model name prefix and artifact key layout (e.g. `tenant_<uuid>/…`) are the gateway's own contract, fixed in implementation, not on the tenant-facing surface.
- **Tenant-scoped experiment & model UI.** The platform-native browsing experience that replaces MLflow's UI (decision 7) is a frontend concern owned by implementation (inherits ADR-0013).
- **Migration of existing tracking data.** Attributing or retiring current single-tenant experiments, runs, models, and artifacts is an operational task (inherits ADR-0013).
- **Retention and quota.** Per-tenant limits on runs, model versions, and artifact storage are deployment configuration, not this ADR (inherits ADR-0013).

## References

- ADR-0013 — experiment-tracking & model-registry multi-tenancy; the gateway and tenant-boundary decisions this ADR realizes.
- ADR-0015 — capability minting + the SQL proxy; the opaque-handle/audience/proxy pattern this ADR follows for the tracking plane.
- ADR-0012 — per-session credential delivery; the kernel holds a capability, not a store credential.
- ADR-0011 — embedded notebooks; decision 9's gate, lifted here for the authoring profile.
- MLflow tracking server "proxied artifact access" (`--serve-artifacts`) — the artifact-proxying mechanism decision 6 relies on.
