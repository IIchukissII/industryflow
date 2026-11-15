<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0013 — Experiment-tracking and model-registry multi-tenancy

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-11-07
- **Parent:** ADR-0001

## Context

1. The tracking stack is one server, one backend and one artifact bucket holding every tenant's
   experiments, runs, models and artifacts.
2. Its authorization is per user, not per tenant, and its UI shows everything.
3. A notebook needs tracking operations, not the store credential behind them.
4. When only trusted code touches the store, logical scoping is the platform's normal mechanism;
   physical separation adds containment at operational cost, not a boundary mediation lacks.

## Decision

1. **The tracking server, its backend and the artifact store are internal; untrusted code never
   holds their credentials, never reaches the tracking server or its backend, and reaches the
   artifact store only under the grants of decision 3.** An authoring environment tracks with a
   per-session capability (ADR-0012).
   Rests on: 2, 3.
2. **All tenant access to tracking and the registry is mediated by a trusted, tenant-scoping
   gateway.** It forces every operation into the caller's tenant namespace, refuses any that would
   cross it, and alone holds the store credentials.
   Rests on: 1, 2, 3.
3. **One tenant dimension covers experiments and runs, registered models and artifacts.** Artifacts
   sit under a per-tenant location; their bytes move directly between caller and store under
   per-object, per-operation, minutes-long grants the gateway issues after checking the tenant.
   Rests on: 1.
4. **The tracking server's own UI is not exposed to tenants.** Tenant browsing is served by the
   platform's tenant-scoped API and UI, which re-check the tenant on every item returned.
   Rests on: 2.
5. **Isolation is realised within one shared store; per-tenant physical separation is a hardening
   step gated on need.** A server, backend or bucket per tenant is the path if mediation proves
   insufficient or a tenant's scale or compliance requires it.
   Rests on: 4.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The tracking server exposed with its own authorization plugin | Its authorization is per user, not per tenant, and it puts the store on the tenant edge |
| 1 | Shared store credentials in notebooks, tenants separated by naming | Broad credentials in untrusted code, cross-tenant by default |
| 1 | Every artifact byte relayed by the gateway | Model-sized transfers through one mediator; a per-object, minutes-long grant reaches only what the gateway checked |
| 2 | No tracking or registration from notebooks | Removes the core of the authoring profile |
| 5 | A server, backend and bucket per tenant now | No boundary the gateway lacks, at fleet cost; kept as the hardening path |

## Consequences

**Positive**
- Authoring environments log runs and register models within their own tenant.
- Tracking and artifact credentials never reach untrusted environments.
- The product shows a tenant its experiments and runs read-only; the tracking server's own UI stays
  internal.

**Negative**
- The boundary rests on the gateway being correct; a bug there is a cross-tenant leak.

## Deferred

| Open question | Settled by |
|---|---|
| Trigger and mechanism for physical separation (decision 5) | A tenant's scale or compliance, or a mediation failure |
| Tracking data recorded outside any tenant namespace: attribute or retire | Maintainer |
| Per-tenant retention and quota for runs, versions and artifacts | First tenant whose usage needs bounding |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): gateway naming, artifact
  locations; manual of decisions 1-5.
- [Tracking gateway](../services/notebook_tracking_gateway/README.md): manual of decisions 1, 2.
