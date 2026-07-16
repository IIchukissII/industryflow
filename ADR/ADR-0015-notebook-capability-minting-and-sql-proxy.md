<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0015: Notebook capability minting and the SQL access proxy

- **ID:** ADR-0015
- **Status:** Accepted (rev 1 — two of this record's claims are no longer true of every plane. Decision 4's "the spawner is the sole minting authority" cannot absorb a plane whose holder never spawns, and decisions 4 and 7 describe every handle as read-only while one plane has been minted read-write since the tracking plane existed. Rev 1 records both, and the conditions a non-spawn minter must meet. The capability's shape — opaque handle, store-as-authority, revocation by deletion, audiences that never interchange — is unchanged)
- **Date:** 2026-06-27 (rev 1: 2026-07-16)
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0012 (per-session credential delivery), ADR-0011 (embedded notebooks), ADR-0014 (hub single sign-on), ADR-0003 (tenant→schema resolution), ADR-0004 (sessions and transport)

## Context and problem

ADR-0012 decided *that* every credential a notebook holds is minted per session by the spawner, scoped to one tenant, read-only, short-lived, and centrally revocable; that the kernel never holds a raw database password; and that direct SQL goes through a trusted proxy that holds the privileged principal and binds each connection to the tenant's read-only role. It deliberately left *how* to a follow-up: the form a capability takes, how it is issued and revoked, and how the SQL proxy resolves a capability to a tenant and binds the connection. ADR-0014 has since established how a user is authenticated to the hub; the spawner now has a verified identity to mint against. This ADR resolves the deferred mechanism.

Two properties from ADR-0012 dominate the design. First, **revocation must be central and immediate, with lifetime only a backstop** (ADR-0012 dec 6): ending a session must stop a capability working without waiting for it to expire. Second, **the capability lives in an untrusted kernel** (ADR-0012 dec 1-2): the user's code can read and copy it, so its authority must be cheap to withdraw and worth little if leaked. These two together point away from a self-contained, signed token whose whole point is to be verifiable *without* a lookup — because immediate revocation of such a token requires a denylist that is itself a lookup against shared state, so you pay for a store and still carry a token that is replayable until it expires. If a lookup against shared state is required for revocation anyway, the capability should *be* a reference into that state, so that the lookup which authorizes it is the same lookup that can revoke it. That is the crux of the form decision below.

The second half is the SQL path. The per-tenant read-only role already exists (ADR-0011 keystone): a `NOLOGIN` role granted exactly one tenant's schema, read-only, designed to be assumed via `SET ROLE`. What is missing is the thing that assumes it on a kernel's behalf without handing the kernel a database password. No off-the-shelf connection pooler resolves an opaque capability to a tenant and switches role per connection; that resolution-and-binding is the specific job this ADR gives a small trusted proxy.

## Decision drivers

- **Immediate central revocation is a requirement, not a nicety (ADR-0012 dec 6).** The capability form must make "revoke now" the normal path, not a denylist bolted onto a token that is otherwise valid until expiry.
- **The capability lives in untrusted code (ADR-0012 dec 1-2).** It must be single-tenant, read-only, audience-bound, and withdrawable, so a copy is worth little and dies quickly.
- **The platform already runs a shared, expiring key-value store.** Redis is present and is the natural home for short-lived, revocable capability state; introducing a new mechanism for this would be unjustified.
- **The per-tenant read-only role is already the boundary (ADR-0011).** The SQL path's job is only to *assume* it safely on the kernel's behalf — the isolation is already enforced by GRANT.
- **No database password in the kernel (ADR-0012 dec 2).** Whatever the kernel presents to reach SQL must be its revocable capability, not a reusable database credential.

## Decision

1. **A capability is an opaque, high-entropy handle backed by a short-lived entry in the shared store, not a self-contained signed token.** The spawner mints a capability by generating a random handle and recording, in the platform's expiring key-value store under that handle, the bound facts: the user, the single tenant (`company_id`), the audience (which plane the handle is for), read-only intent, and an expiry. The handle carries no authority by itself; its authority is the store entry. A verifier authorizes a handle by resolving it in the store, so the same lookup that grants it is the one that can withdraw it.

   > **rev 1 (2026-07-16): read-only is a *bound fact*, and this decision always said so — the later decisions that read as though it were a universal property are the ones that drifted.** This decision lists "read-only intent" among the facts a handle is *bound to*, alongside the user and the tenant: a field with a value, recorded per handle. Decisions 4 and 7 then describe every handle as read-only, which was an accurate description of the two planes that existed and has not been true since the tracking plane arrived — a kernel logging a run and registering a model is writing, and the record introducing that plane quietly stopped listing read-only among its bound facts rather than say so. The code has been minting it read-write throughout.
   >
   > This is recorded here rather than left in a comment, because a decision that no longer describes the system is not repaired by the system agreeing with itself in private. For the planes that are read-only, the tenant *and* the read-only intent bound the handle. For a plane that writes, **the boundary is the tenant namespace the plane's trusted mediator enforces, and read-only is simply false on the binding** — never an exception to it, and never a widening of what the handle can reach: a writing handle still reaches exactly one tenant.

2. **Revocation is deletion of the store entry; expiry is the backstop.** Ending a session — logout, an operator stopping an environment, a detected compromise — deletes the handle's entry, and the capability stops working on its next use (ADR-0012 dec 6). The entry also carries a short time-to-live so that a missed deletion still bounds the window; while a session is healthy the spawner keeps the entry alive. The handle in the pod never has to rotate, because its authority is the centrally-controlled entry, not the handle itself.

3. **Each plane gets its own audience-bound handle; handles are not interchangeable (ADR-0012 dec 5).** The data-API path and the direct-SQL path receive distinct handles whose store entries record distinct audiences. The data API honours only API-audience handles; the SQL proxy honours only SQL-audience handles. Possession of one never yields the other, and neither can be exchanged for anything broader than its recorded tenant and read-only scope.

   > **rev 1 (2026-07-16):** planes have been added to this decision by the records that introduced them, which is the pattern it was written to support and needs no revision. Rev 1 notes only that non-interchangeability has since become **load-bearing in a way rev 0 did not anticipate**: it was written to stop one plane's handle opening another's door. A later plane additionally makes the *audience itself* the fact that decides which admission rules a request faces, so that two populations reaching the same mediator can be held to different standards. Where that is so, collapsing two audiences into one would not merely widen access — it would silently apply one population's rules to the other, in whichever direction happened to be implemented. An audience is now sometimes the only thing distinguishing what a caller *is*, and not just what it may reach.

4. **The spawner is the sole minting authority and injects handles at spawn (ADR-0012 dec 1, ADR-0014).** Using the verified identity established at hub entry (ADR-0014), the spawner mints the handles, writes their store entries scoped to that one tenant and read-only, and injects them into the environment. The kernel receives handles only; it cannot mint, refresh, or widen them, and it never receives the privileged principal behind either plane.

   > **rev 1 (2026-07-16): "sole" was true of every holder this record could see, and a plane now exists whose holder never spawns.** Every handle rev 0 contemplated belongs to a kernel, and a kernel comes into being by being spawned — so the spawner was not merely *a* trusted minter, it was the only moment at which minting could occur. Later planes preserved that (the tracking and cold audiences are both spawn-minted, and each said so). A plane whose holder is a person rather than a kernel has no spawn to hang minting on, and "sole minting authority" cannot be extended into a case it excludes: it must be revised, or the plane must not exist.
   >
   > So minting has **two modes**, and the second is deliberately narrow. *Spawn-minting* stays exactly as this decision describes it, and remains the only way a kernel receives anything. *Demand-minting* is a trusted component minting a handle at the moment a person asks for one, and is permitted only where **all** of the following hold: the minter establishes the tenant from a **verified platform session and never from anything the caller asserts** (ADR-0003 dec 1); the minter is the **designated authority for that plane**, named in the record that introduces the plane, so the question "who may issue this" has one answer rather than a convention; the handle is **consumed immediately** rather than held, and its lifetime reflects one operation rather than a session; and **no session secret is distributed to enforce it** — a demand-minter must already hold session verification for its own reasons, because a plane that spreads the platform's signing key to a component that had no need of it has bought a capability by giving away the thing capabilities exist to protect.
   >
   > What does **not** change: the kernel still cannot mint, refresh, or widen anything, and no minter — of either mode — hands out the privileged principal behind a plane.
   >
   > **The words "and read-only" in this decision describe the planes rev 0 minted, not a property of minting.** See decision 1's rev-1 note.

5. **Direct SQL is reached through a trusted proxy that resolves the handle and assumes the tenant's read-only role.** The kernel connects to the SQL proxy presenting its SQL-audience handle in place of a password; the proxy resolves the handle in the store to its tenant, and — holding the privileged principal that may assume any tenant's reader role — opens a backend connection and `SET ROLE`s into that tenant's `NOLOGIN` read-only role (ADR-0011) before proxying queries. The database password lives only in the proxy; the kernel presents only its revocable handle (ADR-0012 dec 2, dec 4).

6. **The SQL proxy is a small purpose-built component, not an off-the-shelf pooler.** Because the binding step — resolve an opaque handle to a tenant, then assume that tenant's role for the connection — is exactly what general-purpose poolers do not do, the proxy that performs it is purpose-built. A standard pooler may sit behind it for connection pooling, but the handle-to-tenant resolution and role assumption are the proxy's own responsibility and the reason it exists.

7. **The store is the single capability authority; it holds handles and bindings, never database or object-store secrets.** The shared store records only the minted handles and their bound facts. The privileged database principal stays in the proxy, the platform session stays in its cookie (ADR-0004), and neither is placed in the store. A reader of the store sees revocable, single-tenant, read-only handles and nothing whose loss is worse than that.

   > **rev 1 (2026-07-16):** the closing sentence's "read-only" is decision 4's drift, not a further claim — see decision 1's rev-1 note. What this decision actually guarantees is unchanged and is the part that matters: **a reader of the store sees revocable, single-tenant handles, and nothing whose loss is worse than that.** That property is what forbids putting a session-verification key wherever a handle is resolved: the store's whole value is that its worst-case disclosure is bounded, and a resolver that also held the platform's signing key would make the bound a fiction — the loss would no longer be one tenant's plane but every tenant's session.

## Alternatives considered

**A. Self-contained signed tokens (a JWT-style capability) verified without a lookup.** *Rejected as the baseline:* a self-contained token is replayable until it expires, and ADR-0012 dec 6 requires immediate central revocation, which forces a denylist — a lookup against shared state for every use. Once a per-use lookup is unavoidable, the opaque-handle form (decision 1) is strictly simpler: the authorizing lookup and the revoking action are the same, with no signing keys to distribute or rotate. Signed tokens remain the right tool where offline verification is the goal; here it is not.

**B. Mint a real per-tenant database password into the kernel (a credential broker).** *Rejected* and already rejected by ADR-0012 dec 2: it places a reusable database credential in untrusted code and needs either a new secrets engine or bespoke role lifecycle. The proxy of decisions 5-6 reaches the same single-tenant, read-only scope with no database secret in the kernel and with revocation handled by the store.

**C. Use an off-the-shelf connection pooler as the SQL proxy.** *Rejected:* poolers authenticate connections and pool them, but they do not resolve an opaque capability to a tenant and `SET ROLE` per connection — the tenant binding (decision 5) is the entire purpose and is not their job. A pooler may pool behind the purpose-built proxy, but it cannot replace it.

**D. Capabilities bounded only by a time-to-live, with no central revocation.** *Rejected:* this is exactly what ADR-0012 dec 6 forbids — it makes expiry the only control and leaves a leaked or compromised session working until its lifetime lapses.

**E. Mint a fresh handle per request or per query.** *Rejected:* a stable handle whose authority is a centrally-controlled, deletable store entry (decisions 1-2) already gives immediate revocation without asking untrusted code to rotate a secret. Per-request minting adds churn and a refresh path inside the kernel for no isolation gain.

## Consequences

### Positive

- Immediate central revocation is the default path, not an add-on: deleting one store entry withdraws a capability on its next use (decisions 1-2), satisfying ADR-0012 dec 6 directly.
- A leaked capability is worth little — single-tenant, read-only, audience-bound, network-restricted (ADR-0011 dec 7), and revocable — and the kernel never holds a database password or the privileged principal (decisions 3, 5, 7).
- The SQL path reuses the existing GRANT-enforced boundary (ADR-0011): the proxy only *assumes* the per-tenant reader role, so isolation does not rest on the proxy's own query handling being perfect, only on it binding the right role.
- The design reuses infrastructure already in the platform — the shared expiring store and the per-tenant reader role — rather than introducing a secrets engine or a new token system (decisions 1, 7).
- Handles need no rotation in untrusted code, because their authority is the store entry the spawner controls (decision 2), keeping the kernel side simple.

### Negative

- The shared store becomes a capability authority on the hot path: every data-API call and SQL connection resolves a handle against it, so its availability and latency now matter for notebooks, and its compromise would expose live handles (mitigated by what it holds — decision 7 — being only single-tenant read-only handles).
- A new trusted, purpose-built SQL proxy must be built, hardened, and kept available; it holds the privileged principal and is the one component that may assume any tenant's role (decisions 5-6).
- The spawner gains a lifecycle responsibility: minting handles, keeping their entries alive while a session is healthy, and deleting them on session end (decisions 2, 4).
- Presenting the handle as the database password over the Postgres wire constrains how clients connect and must be documented so notebook tooling uses it correctly (decision 5).
- Two honouring points — the data API and the SQL proxy — must both resolve handles and both observe revocation, so the revocation path has more than one place to be correct.

## Deferred decisions

- **Store key schema, time-to-live, and refresh cadence.** The concrete key layout, the capability lifetime, and how often the spawner refreshes a live entry are configuration values owned by the implementation, not this ADR.
- **The SQL proxy's wire-level details and optional pooling.** Exactly how the proxy reads the handle from the connection, whether a standard pooler sits behind it, and connection-pool sizing are implementation choices within decisions 5-6.
- **Experiment-tracking capability.** Whether tracking access (ADR-0013) is gated by a third audience-bound handle of the same form, or folded into the API-audience handle, is left to the tracking-gateway implementation.
- **Capability-use audit.** What record is kept of which handle accessed which tenant, and where, relates to the notebook audit trail deferred in ADR-0011 and is not decided here.
- **Refresh-on-revocation propagation.** The precise mechanism by which a platform logout (ADR-0014) triggers deletion of the associated handles is an implementation detail of the spawner/session lifecycle.

## References

- ADR-0012 — per-session credential delivery; this ADR resolves its deferred capability-format, SQL-proxy-binding, and lifetime/revocation decisions.
- ADR-0011 — the per-tenant `NOLOGIN` read-only role the SQL proxy assumes, and the pod network containment that bounds a leaked handle.
- ADR-0014 — the verified identity the spawner mints capabilities against.
- ADR-0003 — tenant→schema resolution and the role naming the proxy binds.
- ADR-0004 — the platform session and the shared store's role alongside it.
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
