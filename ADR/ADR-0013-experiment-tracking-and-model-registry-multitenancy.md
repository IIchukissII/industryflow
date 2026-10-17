<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0013 — Experiment-tracking and model-registry multi-tenancy

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (tracking from notebooks, dec 9), [ADR-0012](ADR-0012-notebook-credential-delivery.md) (the capability model applied here), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (tenant-scoped API and UI), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (the gateway that realises this record)

## Context

The tracking stack is one server, one backend and one artifact bucket holding every tenant's
experiments, runs, models and artifacts. Its authorization is per user, not per tenant, and its UI shows
everything. A notebook needs tracking operations, not the store credential behind them. When only trusted
code touches the store, logical scoping is the platform's normal mechanism; physical separation adds
containment at operational cost, not a boundary mediation lacks.

## Decision

1. **The tracking server, its backend and the artifact store are internal; untrusted code never holds
   their credentials or reaches them directly.** An authoring environment tracks with a per-session
   capability (ADR-0012).
2. **All tenant access to tracking and the registry is mediated by a trusted, tenant-scoping gateway.**
   It forces every operation into the caller's tenant namespace, refuses any that would cross it, and
   alone holds the store credentials.
3. **One tenant dimension covers experiments and runs, registered models and artifacts.** Artifacts sit
   under a per-tenant location; their bytes move directly between caller and store under per-object,
   per-operation, minutes-long grants the gateway issues after checking the tenant.
4. **The tracking server's own UI is not exposed to tenants.** Tenant browsing is served by the
   platform's tenant-scoped API and UI, which re-check the tenant on every item returned.
5. **Isolation is realised within one shared store; per-tenant physical separation is a hardening step
   gated on need.** A server, backend or bucket per tenant is the path if mediation proves insufficient
   or a tenant's scale or compliance requires it.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The tracking server exposed with its own authorization plugin | Its authorization is per user, not per tenant, and it puts the store on the tenant edge |
| 1 | Shared store credentials in notebooks, tenants separated by naming | Broad credentials in untrusted code, cross-tenant by default |
| 2 | No tracking or registration from notebooks | Removes the core of the authoring profile |
| 5 | A server, backend and bucket per tenant now | No boundary the gateway lacks, at fleet cost; kept as the hardening path |

## Consequences

**Positive**
- Authoring environments log runs and register models within their own tenant.
- Tracking and artifact credentials never reach untrusted environments.

**Negative**
- The boundary rests on the gateway being correct; a bug there is a cross-tenant leak.
- The product UI browses registered models but has no view of experiments and runs.

## Deferred

| Open question | Settled by |
|---|---|
| Trigger and mechanism for physical separation (decision 5) | A tenant's scale or compliance, or a mediation failure |
| Product view of experiments and runs | Maintainer, with the frontend analytics pages |
| Tracking data recorded outside any tenant namespace: attribute or retire | Maintainer |
| Per-tenant retention and quota for runs, versions and artifacts | First tenant whose usage needs bounding |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): gateway naming, artifact locations.
- `services/notebook_tracking_gateway/`: decisions 2 and 3.
