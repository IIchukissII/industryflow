<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0013: Experiment-tracking and model-registry multi-tenancy

- **ID:** ADR-0013
- **Status:** Accepted
- **Date:** 2026-06-27
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0011 (embedded notebooks), ADR-0012 (per-session credential delivery), ADR-0003 (tenant→schema resolution), ADR-0004 (API authentication, sessions and transport)
- **Refined by:** ADR-0019 (the experiment-tracking gateway mechanism that realizes this ADR's decisions and resolves its deferred artifact-access sub-decision)

## Context and problem

ADR-0011 gated notebook access to experiment tracking until the tracking store's multi-tenancy was decided, because the store is single-tenant today: one tracking server backed by one database and one object-store bucket, holding everyone's experiments, runs, registered models, and artifacts with no tenant boundary between them. The moment two tenants' data scientists log experiments — which authoring notebooks (ADR-0011) exist to let them do — they would see each other's runs, models, and artifacts, and the credentials that today reach the tracking and object stores are shared and broad (the standalone Jupyter container already carries the object store's credentials in its environment). This ADR decides how experiment tracking and the model registry become multi-tenant so that gate can be lifted.

The tracking stack has no useful native multi-tenancy to build on. Its open-source authorization is, at best, per-user permissions bolted on, not a tenant model, and its web UI shows whatever the server holds with no notion of a caller's tenant. So isolation cannot come from the tracking server's own features; it has to be imposed around it, the way tenant isolation is imposed everywhere else in this platform — by trusted code that scopes access, never by the untrusted caller.

That framing is what makes this tractable and consistent. ADR-0012 already established the pattern for the hard case: the kernel runs untrusted code, so it gets only narrow per-session capabilities and never the privileged store credential, and direct database access is brokered by a trusted proxy that holds the real credential and binds each connection to the caller's tenant. Experiment tracking is the *easier* case of the same shape, because a notebook does not need raw tracking-store or object-store credentials any more than it needs a raw database password — it needs to log runs and read its own tenant's runs. So the tracking store can stay an internal, trusted-side service, with all tenant access mediated by trusted code, exactly as the query path is mediated by the API gateway (ADR-0003, ADR-0004) and the SQL path by the proxy (ADR-0012). The residual risk — a bug in the mediating code leaking across tenants — is the same residual risk the platform already accepts for its trusted query paths, and is acceptable for the same reason: only trusted code touches the store.

The remaining question is whether to also separate tenants *physically* — a tracking server, backend, and bucket per tenant — or to keep one shared store and let the trusted mediator enforce the boundary logically. Because the untrusted party never touches the store directly, physical separation buys defence-in-depth at real operational cost rather than buying a boundary that logical mediation cannot provide, so it is a hardening step to reach for when warranted, not the starting point.

## Decision drivers

- **The tracking store has no native tenant isolation, so it must be imposed around it.** Its own authorization and UI are not tenant-aware; isolation has to come from trusted code that scopes every access, as it does for every other store in the platform.
- **A notebook needs to track experiments, not to hold store credentials.** As with the database (ADR-0012), the untrusted kernel needs an operation (log a run, read my runs), not the privileged credential behind it; the credential stays in trusted code.
- **Consistency with the platform's existing mediation pattern.** The API gateway and the SQL proxy already hold privileged principals and scope access per tenant from trusted code (ADR-0003, ADR-0004, ADR-0012); experiment tracking should be mediated the same way rather than inventing a new exposure model.
- **The untrusted party never touches the tracking store, so logical isolation here is not isolation-by-discipline-in-untrusted-code.** The distinction ADR-0011 drew — discipline is unacceptable when the disciplined code is user-authored — does not apply when only trusted code reaches the store; trusted-code scoping is the platform's normal, accepted mechanism.
- **Physical per-tenant separation is cost, not a different boundary, here.** Since access is already mediated, a server-and-bucket-per-tenant adds blast-radius containment and operational burden without enabling anything the mediator cannot already enforce; it belongs on the hardening/scaling path, gated on need.

## Decision

1. **The tracking server, its backend, and the artifact store are internal trusted-side services; notebooks and other untrusted code never receive their credentials or reach them directly.** The shared tracking and object-store credentials that untrusted environments hold today are retired from every product surface. Whatever an authoring environment uses to track experiments is a per-session capability (ADR-0012), not a tracking-store or object-store credential.

2. **All tenant access to experiment tracking and the model registry is mediated by a trusted, tenant-scoping gateway.** The gateway authenticates the per-session capability (ADR-0012), forces every tracking and registry operation into the caller's tenant namespace, and refuses any read or write that would cross it. This is the experiment-tracking analogue of the API gateway scoping a query (ADR-0003, ADR-0004) and the SQL proxy scoping a connection (ADR-0012): the privileged store credential lives only in trusted code, and the caller presents only a scoped capability.

3. **A single tenant dimension is enforced consistently across all three stores — experiments and runs, registered models, and artifacts — and no operation crosses it.** Tracking metadata, registry entries, and artifact locations all carry the same tenant boundary (artifacts under a per-tenant location), so a run, a model version, and its artifacts are isolated together. There is no path that isolates one of the three and not the others.

4. **The tracking server's own web UI is not exposed to tenants.** Because that UI has no notion of a caller's tenant, it would show every tenant's experiments; tenant-facing browsing of experiments and models is served by the platform's own tenant-scoped API and UI (ADR-0004), which resolve the tenant the same way the rest of the product does. The tracking UI remains an internal operator tool.

5. **Isolation is realized within a single shared tracking store by the trusted gateway; physical per-tenant separation is a deferred hardening step, gated on need.** One tracking server, one backend, and one bucket are kept, with the boundary enforced by decisions 2 and 3, because the untrusted party never touches the store and trusted-code scoping is the platform's normal mechanism. Moving to a tracking server, backend, or bucket per tenant is retained as the way to add blast-radius containment if the shared store's mediated boundary proves insufficient or a tenant's scale or compliance requires it — not adopted up front.

## Alternatives considered

**A. Stand up a tracking server, backend, and bucket per tenant now.** *Rejected for now:* physical separation is the strongest containment, and it matches the schema-per-tenant instinct — but here it does not provide a boundary the trusted gateway cannot already enforce, because the untrusted kernel never touches the tracking store in the first place. It would add per-tenant provisioning, a fleet of servers, and storage overhead for defence-in-depth the platform does not yet need. It is kept explicitly as the hardening path in decision 5 rather than discarded.

**B. Expose the tracking server directly to tenants using its own authorization plugin.** *Rejected:* the open-source authorization is per-user, not tenant-aware, and would place the tracking store on the tenant-facing edge — the opposite of decision 1. It would make the store's weak, non-tenant authorization the boundary for a multi-tenant product.

**C. Give notebooks the shared tracking and object-store credentials and separate tenants by naming convention.** *Rejected:* this is the status quo of the developer Jupyter container — shared, broad credentials inside untrusted code, cross-tenant by default, with a naming convention as the only "boundary." It is exactly the shared-ambient-credential exposure ADR-0011 decision 2 and ADR-0012 forbid, now over the tracking and artifact stores.

**D. Allow no experiment tracking or model registration from notebooks; permit only what the existing platform API already exposes.** *Rejected:* tracking experiments is core to what the authoring audience (ADR-0011) exists to do; removing it would gut the experimentation surface. The gateway of decision 2 provides tracking safely rather than withholding it.

## Consequences

### Positive

- ADR-0011's gate on notebook experiment tracking can be lifted: authoring environments can log and read experiments and register models within their own tenant, mediated by trusted code (decisions 1, 2).
- The tracking and artifact stores stop being a cross-tenant exposure: their credentials leave untrusted environments entirely, and every access is scoped to the caller's tenant (decisions 1, 2, 3).
- Experiment tracking reuses the platform's established mediation pattern (API gateway, SQL proxy), so there is one model for "trusted code holds the privileged principal and scopes per tenant," not a new one to reason about (decision 2).
- Runs, model versions, and artifacts are isolated together under one tenant dimension, so there is no partial-isolation path where metadata is scoped but artifacts are not, or vice versa (decision 3).
- Keeping a single shared store with logical isolation avoids per-tenant provisioning and a server fleet now, while leaving physical separation available as a clean later step (decision 5).

### Negative

- The tenant boundary for tracking rests on the mediating gateway being correct; a bug there is a cross-tenant leak. This is the same residual risk the platform accepts for its other trusted scoping paths, but it is real and concentrated in one component (decisions 2, 5).
- A new trusted component — the tracking/registry gateway — must be built, hardened, and kept available on the path between notebooks and the tracking store (decision 2).
- The tracking server's native UI, which data scientists may expect, is not available to them; an equivalent tenant-scoped browsing experience must be provided through the platform's own UI instead (decision 4).
- Artifact handling must be made tenant-scoped without putting broad object-store credentials in the kernel, which constrains how large artifacts move and is left as a sub-decision (see Deferred decisions).
- Existing single-tenant tracking data predates the tenant dimension and will need attributing or migrating before the boundary is complete (see Deferred decisions).

## Deferred decisions

- **Artifact-access mechanism.** Whether artifact bytes are proxied through the gateway or reached with per-session, tenant-prefix-scoped, short-lived object-store credentials (the artifact analogue of ADR-0012's scoped capability) is a security-and-performance sub-decision left to its own resolution; this ADR fixes only that no broad object-store credential reaches the kernel and that artifacts carry the tenant boundary (decision 3).
- **When to move to physical per-tenant separation.** The trigger conditions and mechanism for the hardening path in decision 5 — per-tenant server, backend, or bucket — are deferred until need is demonstrated.
- **Tenant-scoped experiment and model UI.** The shape of the tenant-facing browsing experience that replaces the tracking server's native UI (decision 4) is a frontend concern owned by implementation.
- **Migration of existing tracking data.** How current single-tenant experiments, runs, models, and artifacts are attributed to a tenant or retired is an operational task, not decided here.
- **Retention and quota.** Per-tenant limits on runs, model versions, and artifact storage are configuration values owned by the deployment, not this ADR.

## References

- ADR-0011 — embedded notebooks; this ADR resolves its deferred experiment-tracking multi-tenancy decision and lifts the gate in its decision 9.
- ADR-0012 — per-session credential delivery; the capability model and trusted-proxy pattern this ADR applies to the tracking and artifact stores.
- ADR-0003 — tenant→schema resolution and the trusted-code scoping the tracking gateway mirrors.
- ADR-0004 — the platform session, per-tenant authorization, and tenant-scoped UI that serve experiment browsing.
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
