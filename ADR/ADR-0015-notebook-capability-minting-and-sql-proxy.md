<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0015: Notebook capability minting and the SQL access proxy (rev 3)

- **ID:** ADR-0015
- **Status:** Accepted
- **Date:** 2026-06-27 (rev 1: 2026-07-16; rev 2: 2026-10-13; rev 3: 2026-10-13)
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0012 (per-session credential delivery), ADR-0011 (embedded notebooks), ADR-0014 (hub single sign-on), ADR-0003 (tenant→schema resolution), ADR-0004 (sessions and transport)
- **Supersedes:** ADR-0015 rev 0 (2026-06-27 — described every handle as spawn-minted and read-only); rev 1 (2026-07-16 — records demand-minting and writing planes, but kept the SQL proxy's upstream session as one principal able to switch into any tenant's reader; rev 2 confines the session principal itself to one tenant, decision 5); rev 2 (2026-10-13 — left when a handle is refreshed, when it is revoked, and what happens to a SQL session already open, to the implementation; rev 3 decides them, decisions 2 and 4)

## Context and problem

ADR-0012 decided *that* every credential a notebook holds is minted per session by the spawner, scoped to one tenant, read-only, short-lived, and centrally revocable; that the kernel never holds a raw database password; and that direct SQL goes through a trusted proxy that holds the privileged principal and binds each connection to the tenant's read-only role. It deliberately left *how* to a follow-up: the form a capability takes, how it is issued and revoked, and how the SQL proxy resolves a capability to a tenant and binds the connection. ADR-0014 has since established how a user is authenticated to the hub; the spawner now has a verified identity to mint against. This ADR resolves the deferred mechanism.

Two properties from ADR-0012 dominate the design. First, **revocation must be central and immediate, with lifetime only a backstop** (ADR-0012 dec 6): ending a session must stop a capability working without waiting for it to expire. Second, **the capability lives in an untrusted kernel** (ADR-0012 dec 1-2): the user's code can read and copy it, so its authority must be cheap to withdraw and worth little if leaked. These two together point away from a self-contained, signed token whose whole point is to be verifiable *without* a lookup — because immediate revocation of such a token requires a denylist that is itself a lookup against shared state, so you pay for a store and still carry a token that is replayable until it expires. If a lookup against shared state is required for revocation anyway, the capability should *be* a reference into that state, so that the lookup which authorizes it is the same lookup that can revoke it. That is the crux of the form decision below.

The second half is the SQL path. The per-tenant read-only role already exists (ADR-0011 keystone): a `NOLOGIN` role granted exactly one tenant's schema, read-only, designed to be assumed via `SET ROLE`. What is missing is the thing that assumes it on a kernel's behalf without handing the kernel a database password. No off-the-shelf connection pooler resolves an opaque capability to a tenant and switches role per connection; that resolution-and-binding is the specific job this ADR gives a small trusted proxy.

**Revision 1 (2026-07-16).** Two of this record's claims stopped being true of every plane. Decision 4's "the spawner is the sole minting authority" cannot absorb a plane whose holder never spawns, and decisions 4 and 7 describe every handle as read-only while the tracking plane has been minted read-write since it existed. Rev 1 records both, and the conditions a non-spawn minter must meet. The capability's shape — opaque handle, store as the authority, revocation by deletion, audiences that never interchange — is unchanged. The rev-1 notes sit under decisions 1, 3, 4 and 7.

**Revision 2 (2026-10-13).** Decision 5 put the tenant boundary for direct SQL in a role switch: the proxy logged in as one shared principal that was a member of every tenant's reader role, and switched the session into the caller's reader before relaying. Review found that the switch is not a boundary, because the session that performs it is then handed to the kernel. A kernel speaking SQL on that session can issue the same switch itself — into another tenant's reader role, which the shared principal is equally entitled to assume — or undo it. The grants on each reader role were correct; the session principal sat above all of them. A boundary a client can step back across is not a boundary, and the property ADR-0011 dec 3 decided — a cross-tenant query *fails*, by privilege, whatever the kernel sends — did not hold on this path. Rev 2 moves the boundary to the session principal itself (decision 5).

**Revision 3 (2026-10-13).** Decision 2 made revocation the normal path and expiry the backstop, then deferred *when* revocation happens and how it reaches the honouring points. Review found the deferral had been filled with less than the decision promised. Handles were kept alive on a timer whether or not the environment holding them still existed; stopping or respawning an environment left its old handles valid until they expired; a platform logout revoked nothing; and a SQL session, once open, was never checked again, so revoking its handle stopped new connections and left the open one working. Separately, the upload plane's demand-minted handle (ADR-0030 dec 3) could be presented more than once within its lifetime, although decision 4 rev 1 says it is consumed. Rev 3 states the lifecycle (decision 2) and what "consumed" means (decision 4). Rev 2's per-tenant session principal is unchanged and not repeated here.

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

   > **rev 3 (2026-10-13): the lifecycle, stated.** A spawn-minted handle's entry is **refreshed only while the environment holding it is running**, as the spawner observes it; nothing refreshes the entry of an environment that has gone. Its entries are **revoked when the environment stops, before it is respawned** (so a respawn never leaves its predecessor's handles valid beside the new ones), and **when the user signs out** — the hub's chained logout stops the environment (ADR-0014 rev 1 dec 7), and ending a platform session revokes the user's capabilities directly (ADR-0004 dec 2 rev 2). Revocation also runs the other way: an environment whose handles have all been revoked centrally is stopped at the spawner's next observation, rather than left running with no planes.
   >
   > Revocation reaches work already in progress. A SQL session opened under a handle is **re-checked at a bounded interval** for as long as it stays open: the proxy resolves the handle again, and closes the session if it no longer resolves, resolves to a different binding, or cannot be checked because the store is unavailable. Every honouring point fails closed the same way on resolution: an entry that is missing, unreadable, or not a complete binding of its own audience denies. Refresh cadence, lifetime and re-check interval are configuration.

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

   > **rev 3 (2026-10-13): a demand-minted handle is spent by its one operation.** "Consumed immediately" means the handle is **used up** by the step that completes the operation it authorises: that step removes the entry atomically, before acting, so a second presentation is refused even while the handle's lifetime has not run out, and two concurrent presentations cannot both succeed. Steps that only prepare the operation do not spend it. Its lifetime remains the backstop for an operation that is never completed.

5. **Direct SQL is reached through a trusted proxy that resolves the handle and assumes the tenant's read-only role.** The kernel connects to the SQL proxy presenting its SQL-audience handle in place of a password; the proxy resolves the handle in the store to its tenant, and — holding the privileged principal that may assume any tenant's reader role — opens a backend connection and `SET ROLE`s into that tenant's `NOLOGIN` read-only role (ADR-0011) before proxying queries. The database password lives only in the proxy; the kernel presents only its revocable handle (ADR-0012 dec 2, dec 4).

   > **rev 2 (2026-10-13): the tenant boundary holds at the session principal, never at a switch the client can undo.** The upstream session the proxy opens for a kernel is authenticated as an identity that is **itself confined to one tenant**: each tenant has its own login identity, whose credential only the proxy holds, and which can reach nothing beyond that tenant's read-only grants — it is a member of no other tenant's role and has no role it could switch into that would widen it. The proxy resolves the handle to its tenant and opens the session *as that tenant's identity*; whatever the kernel then sends, the most it can reach is the one tenant the handle was bound to. A shared principal that can assume every tenant's role, and relies on a role switch to narrow itself, is excluded: the switch is a statement on the same session the kernel controls, so it can be repeated or reversed.
   >
   > What does not change: the kernel still presents only its handle and never a database credential; the per-tenant credentials live only in the proxy, so "may act as any tenant" is still a privilege only the proxy holds (ADR-0012 dec 4) — now as many single-tenant identities rather than one identity with every tenant inside it. Decision 6's "assume that tenant's role" is read accordingly: the proxy binds the connection by *which identity it logs in as*, not by a statement issued after login.

6. **The SQL proxy is a small purpose-built component, not an off-the-shelf pooler.** Because the binding step — resolve an opaque handle to a tenant, then assume that tenant's role for the connection — is exactly what general-purpose poolers do not do, the proxy that performs it is purpose-built. A standard pooler may sit behind it for connection pooling, but the handle-to-tenant resolution and role assumption are the proxy's own responsibility and the reason it exists.

7. **The store is the single capability authority; it holds handles and bindings, never database or object-store secrets.** The shared store records only the minted handles and their bound facts. The privileged database principal stays in the proxy, the platform session stays in its cookie (ADR-0004), and neither is placed in the store. A reader of the store sees revocable, single-tenant, read-only handles and nothing whose loss is worse than that.

   > **rev 1 (2026-07-16):** the closing sentence's "read-only" is decision 4's drift, not a further claim — see decision 1's rev-1 note. What this decision actually guarantees is unchanged and is the part that matters: **a reader of the store sees revocable, single-tenant handles, and nothing whose loss is worse than that.** That property is what forbids putting a session-verification key wherever a handle is resolved: the store's whole value is that its worst-case disclosure is bounded, and a resolver that also held the platform's signing key would make the bound a fiction — the loss would no longer be one tenant's plane but every tenant's session.

## Alternatives considered

**A. Self-contained signed tokens (a JWT-style capability) verified without a lookup.** *Rejected as the baseline:* a self-contained token is replayable until it expires, and ADR-0012 dec 6 requires immediate central revocation, which forces a denylist — a lookup against shared state for every use. Once a per-use lookup is unavoidable, the opaque-handle form (decision 1) is strictly simpler: the authorizing lookup and the revoking action are the same, with no signing keys to distribute or rotate. Signed tokens remain the right tool where offline verification is the goal; here it is not.

**B. Mint a real per-tenant database password into the kernel (a credential broker).** *Rejected* and already rejected by ADR-0012 dec 2: it places a reusable database credential in untrusted code and needs either a new secrets engine or bespoke role lifecycle. The proxy of decisions 5-6 reaches the same single-tenant, read-only scope with no database secret in the kernel and with revocation handled by the store.

**C. Use an off-the-shelf connection pooler as the SQL proxy.** *Rejected:* poolers authenticate connections and pool them, but they do not resolve an opaque capability to a tenant and `SET ROLE` per connection — the tenant binding (decision 5) is the entire purpose and is not their job. A pooler may pool behind the purpose-built proxy, but it cannot replace it.

**D. Capabilities bounded only by a time-to-live, with no central revocation.** *Rejected:* this is exactly what ADR-0012 dec 6 forbids — it makes expiry the only control and leaves a leaked or compromised session working until its lifetime lapses.

**E. Mint a fresh handle per request or per query.** *Rejected:* a stable handle whose authority is a centrally-controlled, deletable store entry (decisions 1-2) already gives immediate revocation without asking untrusted code to rotate a secret. Per-request minting adds churn and a refresh path inside the kernel for no isolation gain.

**F. Keep one shared upstream principal and stop the kernel from issuing role switches *(rev 2)*.** *Rejected:* it moves the boundary into the proxy's understanding of SQL text. A statement filter must recognise every spelling of a role change, in every protocol path and function that can perform one, and it fails open on the first it misses. The boundary would then rest on the proxy's parsing being perfect — the very dependency the per-tenant grants were chosen to remove. Confining the session principal makes the boundary a property of who is logged in, which the database enforces regardless of what is sent.

**G. Refresh every handle on a timer and rely on expiry for stopped environments *(rev 3)*.** *Rejected:* it makes expiry the control for the commonest way a session ends, which decision 2 and ADR-0012 dec 6 rule out, and it keeps a stopped environment's handles valid for a full lifetime after the environment is gone.

**H. Check a SQL handle only when the connection opens *(rev 3)*.** *Rejected:* a connection can stay open for as long as a kernel likes, so revocation would stop new connections and leave the one that matters working. The periodic re-check bounds how long a revoked handle keeps reach to one interval.

## Consequences

### Positive

- Immediate central revocation is the default path, not an add-on: deleting one store entry withdraws a capability on its next use (decisions 1-2), satisfying ADR-0012 dec 6 directly.
- A leaked capability is worth little — single-tenant, read-only, audience-bound, network-restricted (ADR-0011 dec 7), and revocable — and the kernel never holds a database password or the privileged principal (decisions 3, 5, 7).
- The SQL path reuses the existing GRANT-enforced boundary (ADR-0011): isolation does not rest on the proxy's own query handling being perfect, only on it binding the right identity. **rev 2:** that is true only because the bound identity is confined to one tenant at login (decision 5); while it was a shared principal narrowed by a role switch, the kernel could widen it again.
- The design reuses infrastructure already in the platform — the shared expiring store and the per-tenant reader role — rather than introducing a secrets engine or a new token system (decisions 1, 7).
- Handles need no rotation in untrusted code, because their authority is the store entry the spawner controls (decision 2), keeping the kernel side simple.

### Negative

- The shared store becomes a capability authority on the hot path: every data-API call and SQL connection resolves a handle against it, so its availability and latency now matter for notebooks, and its compromise would expose live handles (mitigated by what it holds — decision 7 — being only single-tenant read-only handles).
- A new trusted, purpose-built SQL proxy must be built, hardened, and kept available; it holds the privileged principal and is the one component that may assume any tenant's role (decisions 5-6).
- **rev 2:** the proxy holds one database credential per tenant rather than one in total, so tenant provisioning and offboarding must create and remove that identity and its credential alongside the tenant's schema and reader grants, and credential rotation is per tenant.
- The spawner gains a lifecycle responsibility: minting handles, keeping their entries alive while a session is healthy, and deleting them on session end (decisions 2, 4).
- **rev 3:** the proxy re-resolves every open SQL session's handle on an interval, so the store sees load proportional to open sessions as well as to new ones, and a store outage closes open SQL sessions rather than leaving them unchecked.
- Presenting the handle as the database password over the Postgres wire constrains how clients connect and must be documented so notebook tooling uses it correctly (decision 5).
- Two honouring points — the data API and the SQL proxy — must both resolve handles and both observe revocation, so the revocation path has more than one place to be correct.

## Deferred decisions

- **Store key schema, time-to-live, and refresh cadence.** The concrete key layout, the capability lifetime, and how often the spawner refreshes a live entry are configuration values owned by the implementation, not this ADR. *(rev 3: when an entry is refreshed and when it is revoked is decided in decision 2; the values stay here.)*
- **The SQL proxy's wire-level details, optional pooling, and how it stores and rotates the per-tenant credentials (rev 2).** Exactly how the proxy reads the handle from the connection, whether a standard pooler sits behind it, and connection-pool sizing are implementation choices within decisions 5-6.
- ~~**Experiment-tracking capability.**~~ *Resolved by ADR-0019:* tracking is gated by its own audience-bound handle of the same form, not folded into the API audience.
- **Capability-use audit.** What record is kept of which handle accessed which tenant, and where, relates to the notebook audit trail deferred in ADR-0011 and is not decided here.
- ~~**Refresh-on-revocation propagation.**~~ *Resolved in rev 3, decision 2:* a platform sign-out reaches the handles by the hub's chained logout and by the platform revoking the user's capabilities when the session ends; open SQL sessions observe it at their next re-check.

## Resolved since acceptance

- **How the proxy holds and rotates the per-tenant credentials (rev 2).** The proxy stores none. Each tenant's credential is derived when needed from one secret that only the proxy holds, and the database keeps only a verifier for it. Tenant provisioning creates the tenant's confined login without a credential; the proxy installs the verifier on first use, through a separate enrolment identity that is a member of no role, can perform only that one act, and never carries a kernel's session. Rotating the one secret therefore rotates every tenant's credential at once, each login being re-enrolled at its next refused authentication. The rev 2 consequence that credential rotation is per tenant reads accordingly: each tenant still has its own credential, but rotation is a single act.
- **The binding after login.** A session opened as the tenant's login then assumes the tenant's read-only role, because the login does not inherit it. That step is not the boundary of decision 5 rev 2: a kernel that undoes it is left on the same confined login.
- **Pooling.** No pooler sits behind the proxy; each kernel connection is one upstream session.

## References

- ADR-0012 — per-session credential delivery; this ADR resolves its deferred capability-format, SQL-proxy-binding, and lifetime/revocation decisions.
- ADR-0011 — the per-tenant `NOLOGIN` read-only role the SQL proxy assumes, and the pod network containment that bounds a leaked handle.
- ADR-0014 — the verified identity the spawner mints capabilities against.
- ADR-0003 — tenant→schema resolution and the role naming the proxy binds.
- ADR-0004 — the platform session and the shared store's role alongside it.
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
