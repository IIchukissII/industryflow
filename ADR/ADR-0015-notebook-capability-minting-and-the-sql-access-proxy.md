<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0015 — Notebook capability minting and the SQL access proxy

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-11-09
- **Parent:** ADR-0001

## Context

1. Revocation must be immediate and the capability lives in untrusted code (ADR-0012).
2. Revoking a self-contained token needs a shared-state lookup anyway; the platform already runs a
   shared expiring store.
3. No general-purpose pooler resolves a capability to a tenant, and a session narrowed by a
   statement is one the client can widen again.

## Decision

1. **A capability is an opaque, high-entropy handle backed by a short-lived entry in the shared
   store.** The entry binds user, one tenant, audience, whether the plane may write, and expiry; the
   lookup that authorizes a handle is the one revocation defeats.
   Rests on: 1, 2.
2. **Revocation is deletion of the entry; expiry is the backstop.** A spawn-minted entry is
   refreshed only while its environment runs and is revoked when it stops, before a respawn, and at
   sign-out; an open SQL session is re-checked at a bounded interval and closed when its handle no
   longer resolves to the same binding or cannot be checked, and every honouring point denies a
   missing or incomplete entry.
   Rests on: 1, 2.
3. **Each plane gets its own audience-bound handle; handles are not interchangeable.** A mediator
   honours only its own audience, which also decides the admission rules where two populations meet.
   Rests on: 1.
4. **Minting has two modes: spawn-minting for every kernel handle, and narrow demand-minting.** The
   spawner mints a kernel's handles at spawn from the verified hub identity; the kernel cannot mint,
   refresh or widen them. Demand-minting requires a tenant from a verified session only, one
   designated minter named by the plane's record, a handle spent atomically by the step completing
   its one operation, and no session secret distributed to enforce it.
   Rests on: 1.
5. **Direct SQL is reached through a trusted proxy that opens each upstream session as the tenant's
   own confined login identity.** That identity reaches only its tenant's read-only grants and can
   switch into nothing wider, so whatever the kernel sends reaches one tenant; the role it then
   assumes is not the boundary.
   Rests on: 1, 3.
6. **The SQL proxy is a small purpose-built component, not an off-the-shelf pooler.** Each kernel
   connection is one upstream session.
   Rests on: 3.
7. **The store is the single capability authority and holds handles and bindings, never database or
   object-store secrets.** A reader of it sees revocable, single-tenant handles and nothing worse.
   Rests on: 1, 2.
8. **The store removes a key only when told to or when its expiry passes; a full store refuses
   writes; a store that cannot show it was rebuilt grants nothing.** It holds denials whose absence
   grants, a tenant's suspension and a session's end among them, so a key dropped for memory fails
   open, while a refused write fails the mint or request that needed it. A store lost whole fails
   open the same way, so it is trusted only once it carries a rebuild record: the platform API
   re-derives every suspension from the registry, then writes the record, whose time ends every
   session issued before it; until then every verifier and minter refuses.
   Rests on: 1, 2.

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
| 8 | Evicting the least recently used key when full | A suspension or session-end marker unread for a while is the first to go, and the tenant or session it denied is honoured again |
| 8 | Evicting only keys that carry an expiry | A session-end marker carries one, and the session it ended is honoured again |
| 8 | A larger memory limit with eviction left on | Postpones the failure and keeps it silent |
| 8 | A separate store for what may be evicted | A second store to run for caches that already expire on their own |
| 8 | Rebuilding the denials only by an operator after a loss | Until someone runs it, every plane honours what the lost denials withheld, and nothing shows the gap |
| 8 | Rebuilding only when the platform API starts | A store recreated under running services is not rebuilt |
| 8 | Re-deriving each ended session | No source outside the store records them; ending every earlier session is the only answer that is not a guess |

## Consequences

**Positive**
- Deleting one entry withdraws a capability on its next use, including on open SQL sessions.
- SQL isolation rests on database grants to a confined login, not on the proxy's query handling.

**Negative**
- The store is on the hot path; an outage denies capabilities and closes open SQL sessions.
- A lost store signs every user out and stops every notebook server's handles, and refuses
  everything until the platform API has rebuilt it.
- A store that fills refuses mints, sign-ins and cache writes until memory is freed, so its use is
  watched against its limit.
- Provisioning and offboarding create and remove each tenant's login, whose credential derives from
  one proxy-held secret, enrolled on first use; rotating that secret re-enrols every tenant.
- Notebook SQL clients present the handle as the database password.

## Deferred

| Open question | Settled by |
|---|---|
| None | — |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): audiences, lifecycle values,
  SQL proxy; manual of decisions 1-8.
- [SQL proxy](../services/notebook_sql_proxy/README.md): manual of decisions 2, 5, 6.
