<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0012 — Per-session credential delivery for notebook environments

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-29
- **Parent:** ADR-0001
- **Related:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (the kernel invariants delivered here), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (the session a capability must not impersonate), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (capability form, minting, SQL proxy), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) and [ADR-0030](ADR-0030-externally-authored-model-admission.md) (writing planes), [ADR-0042](ADR-0042-capability-use-audit.md) (the capability-use record)

## Context

Any credential a kernel holds can be read and copied by user code, so the goal is the smallest,
fastest-revocable, least-transferable credential, not concealment. Egress limits and single-tenant,
read-only data reach (ADR-0011) already bound a leak. A database password is durable, usable by any
client and revocable only by rotation. The API gateway already holds the privileged role and scopes each
request to the caller's tenant (ADR-0003).

## Decision

1. **Every credential a notebook environment holds is per session, one user, one tenant, short-lived
   and centrally revocable.** A kernel's credentials are minted at spawn by the trusted spawner and
   cannot be minted, refreshed or exchanged by the kernel; a plane held by a person may be minted on
   demand (ADR-0015 dec 4).
2. **The kernel never holds a raw database password.** Decision 4 serves SQL without one.
3. **The default data path is a per-session capability that authenticates to the data API as the
   user.** It is bound to the data API, one tenant and identity, and is not the session cookie nor
   exchangeable for a session.
4. **Direct SQL goes through an authenticating proxy; the kernel presents only its capability.** The
   proxy opens the upstream session as that tenant's own read-only identity, a member of no other
   tenant's role, so "may act as any tenant" is the proxy's alone, held as many single-tenant identities.
5. **Capabilities are minimal and non-escalatable.** A capability carries the tenant, the identity, its
   one plane and whether that plane may write, and cannot be presented elsewhere or broadened; a writing
   plane is bounded by the tenant namespace its mediator enforces.
6. **Revocation is central and immediate; lifetime is the backstop.** Ending a session invalidates its
   capabilities at every honouring point without waiting for expiry (ADR-0015 dec 2).

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Every plane minted only by the spawner | A plane held by a person has no spawn to mint at |
| 2 | A short-lived database password in the kernel from a credential broker | A database password in user code, needing a secrets engine or race-prone role lifecycle |
| 2 | A long-lived credential shared by a tenant's environments | A standing shared secret with no attribution and revocation only by rotation |
| 3 | The user's platform session in the kernel | Hands over the whole authenticated surface, and revoking it ends the web session |
| 4 | Only an HTTP query service, no direct SQL | Not direct SQL for data-science tools; compatible as an addition |
| 4 | One shared proxy principal narrowed after login | The kernel can repeat or undo the narrowing on its own session |
| 5 | Read-only intent fixed on every capability | Experiment tracking and model upload are required writes |

## Consequences

**Positive**
- No shared, standing or tenant-crossing secret and no database password reaches the kernel.
- A leaked capability is single-tenant, single-plane, usable only inside the egress allowlist.

**Negative**
- The SQL proxy is a trusted component on the data path to harden and keep available.
- Every plane has its own honouring point, and each must observe revocation correctly.

## Deferred

| Open question | Settled by |
|---|---|
| A query-over-HTTP data service in addition (dec 4 alternatives) | First workload that needs it |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): audiences and capability lifecycle.
