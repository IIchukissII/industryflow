<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0015 — Notebook capability minting and the SQL access proxy

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0012](ADR-0012-notebook-credential-delivery.md) (the properties realised here), [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (per-tenant read-only role), [ADR-0014](ADR-0014-notebook-hub-single-sign-on.md) (the identity minted against; chained sign-out), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (session end revokes capabilities), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md), [ADR-0030](ADR-0030-externally-authored-model-admission.md) (planes added since)

## Context

Revocation must be immediate and the capability lives in untrusted code (ADR-0012). Revoking a
self-contained token needs a shared-state lookup anyway; the platform already runs a shared expiring
store. No general-purpose pooler resolves a capability to a tenant, and a session narrowed by a statement
is one the client can widen again.

## Decision

1. **A capability is an opaque, high-entropy handle backed by a short-lived entry in the shared store.**
   The entry binds user, one tenant, audience, whether the plane may write, and expiry; the lookup that
   authorizes a handle is the one revocation defeats.
2. **Revocation is deletion of the entry; expiry is the backstop.** A spawn-minted entry is refreshed
   only while its environment runs and is revoked when it stops, before a respawn, and at sign-out; an
   open SQL session is re-checked at a bounded interval and closed when its handle no longer resolves
   to the same binding or cannot be checked, and every honouring point denies a missing or incomplete
   entry.
3. **Each plane gets its own audience-bound handle; handles are not interchangeable.** A mediator
   honours only its own audience, which also decides the admission rules where two populations meet.
4. **Minting has two modes: spawn-minting for every kernel handle, and narrow demand-minting.** The
   spawner mints a kernel's handles at spawn from the verified hub identity; the kernel cannot mint,
   refresh or widen them. Demand-minting requires a tenant from a verified session only, one designated
   minter named by the plane's record, a handle spent atomically by the step completing its one
   operation, and no session secret distributed to enforce it.
5. **Direct SQL is reached through a trusted proxy that opens each upstream session as the tenant's own
   confined login identity.** That identity reaches only its tenant's read-only grants and can switch
   into nothing wider, so whatever the kernel sends reaches one tenant; the role it then assumes is not
   the boundary.
6. **The SQL proxy is a small purpose-built component, not an off-the-shelf pooler.** Each kernel
   connection is one upstream session.
7. **The store is the single capability authority and holds handles and bindings, never database or
   object-store secrets.** A reader of it sees revocable, single-tenant handles and nothing worse.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Self-contained signed tokens | Replayable until expiry, and revocation needs a per-use lookup anyway |
| 1 | Read-only fixed on every handle | Experiment tracking and model upload are writing planes |
| 2 | Lifetime-only capabilities | Expiry would be the only control (ADR-0012 dec 6) |
| 2 | A fresh handle per request | Churn and a refresh path in the kernel for no isolation gain |
| 2 | Timer refresh, with expiry for stopped environments | A stopped environment's handles stay valid for a full lifetime |
| 2 | Checking a SQL handle only at connect | Revocation would leave open sessions working |
| 4 | The spawner as the sole minting authority for every plane | A plane held by a person has no spawn to mint at |
| 5 | A per-tenant database password in the kernel | A database credential in untrusted code (ADR-0012 dec 2) |
| 5 | One shared upstream principal narrowed by a role switch | The switch is a statement on the session the kernel drives, so it can be repeated into another tenant or undone |
| 5 | One shared upstream principal plus a filter on role switches | The boundary would rest on parsing every spelling of a role change |
| 6 | An off-the-shelf pooler as the proxy | It does not resolve a handle to a tenant |
| 6 | A pooler behind the proxy sharing upstream sessions | A pooled session would outlive or be shared beyond the one handle it was opened for |

## Consequences

**Positive**
- Deleting one entry withdraws a capability on its next use, including on open SQL sessions.
- SQL isolation rests on database grants to a confined login, not on the proxy's query handling.

**Negative**
- The store is on the hot path; an outage denies capabilities and closes open SQL sessions.
- Provisioning and offboarding create and remove each tenant's login, whose credential derives from one
  proxy-held secret, enrolled on first use; rotating that secret re-enrols every tenant.
- Notebook SQL clients present the handle as the database password.

## Deferred

| Open question | Settled by |
|---|---|
| Capability-use audit: which handle accessed which tenant, where recorded | Maintainer, with ADR-0011's execution audit |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): audiences, lifecycle values, SQL proxy.
- `services/notebook_hub/capabilities.py`, `services/notebook_sql_proxy/`: decisions 1–2, 5–6.
