<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0012: Per-session credential delivery for notebook environments

- **ID:** ADR-0012
- **Status:** Proposed
- **Date:** 2026-06-27
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0011 (embedded notebooks for analytics and experimentation), ADR-0003 (tenant→schema resolution), ADR-0004 (API authentication, sessions and transport)

## Context and problem

ADR-0011 decided that notebooks run as per-user isolated environments executing untrusted, user-authored code, that a kernel holds no shared or tenant-crossing credential, that the default data path is the tenant-scoped API called as the user, and that an authoring environment may additionally hold a short-lived, read-only, single-tenant database principal. It deliberately deferred the one piece that makes those decisions real: *how* a credential reaches a kernel without becoming the very thing ADR-0011 forbids. This ADR resolves that deferred decision.

The difficulty is that a kernel is a hostile place to keep a secret. Whatever credential the kernel holds, the user's code can read, copy, and use — that is the definition of an environment that runs arbitrary code. So the question is not "how do we hide a secret from the user" (we cannot) but "what is the smallest, most quickly revocable, least transferable credential that still lets a notebook do its job," and "what must never be placed there at all." Two things make this tractable rather than hopeless. First, ADR-0011 decision 7 already confines a notebook environment's network reach to an egress allowlist, so a leaked credential is useless from outside the cluster. Second, ADR-0011 decision 3 already scopes notebook data access to a single tenant, read-only, so the worst a user can do with their own credential is read data they are already entitled to read. The credential-delivery design must preserve both of those containments and add a third the deferred decision named explicitly: a *raw database password must never enter the kernel at all*, because a database password is a transferable, durable, hard-to-revoke secret of a kind categorically worse than a narrowly scoped capability.

There is a model already in the codebase for how to do this. The API gateway holds the privileged tenant-data role and scopes each request to the caller's tenant in trusted code (ADR-0003, ADR-0004); the caller never holds the database credential, only a session that the gateway exchanges for scoped access per request. The kernel's direct-SQL path has the same shape — an untrusted caller that needs tenant-scoped data — and should be solved the same way, rather than by handing the untrusted caller the database credential the rest of the platform is careful never to expose.

## Decision drivers

- **A kernel cannot keep a secret, so the secret must be cheap to lose.** Every credential placed in an environment running user code is readable by that code. The design goal is therefore minimal scope, fast central revocation, and non-transferability — not concealment.
- **A database password is the wrong kind of credential to expose.** It is durable, broadly usable by any client, and revocable only by rotation or role lifecycle. ADR-0011's deferred decision singled out keeping it out of the kernel; this driver makes that the design's first constraint.
- **The platform already brokers tenant-scoped access from trusted code.** The API gateway holds the privileged principal and scopes per request (ADR-0003, ADR-0004); the same pattern, not a new credential-exposure model, should serve the kernel's SQL path.
- **Revocation must not wait for expiry.** Ending a session — logout, an operator killing a runaway environment, a detected compromise — must invalidate that environment's credentials promptly, with a short lifetime only as the backstop, not the primary control.
- **One trust anchor mints all credentials.** The thing that validates the platform session and spawns the environment (ADR-0011) is the only thing that should mint that environment's credentials; the kernel must never be able to mint, refresh into, or exchange for anything broader than it was given.

## Decision

1. **Every credential a notebook environment holds is minted per session, at spawn time, by the trusted spawner, scoped to exactly one user and one tenant, short-lived, and centrally revocable.** The kernel receives nothing standing, nothing shared across users or sessions, and nothing whose scope exceeds the single tenant and identity that opened the environment. The spawner validates the platform session (ADR-0004) and is the sole minting authority; a kernel cannot mint, refresh, or exchange its credentials for broader ones.

2. **The kernel never holds a raw database password.** The durable, transferable database credential is categorically excluded from the environment. This is the hard line the deferred decision named, and decision 4 is how the SQL path is served without crossing it.

3. **The default data path is a per-session capability that authenticates to the data API as the user.** For the API path of ADR-0011 decision 4, the environment carries a narrow bearer capability — audience-restricted to the data API, scoped to the one tenant and identity, short-lived — that is *not* the user's session cookie and cannot be refreshed or exchanged into a full session. It authorizes reading the user's own tenant data through the existing API and nothing else.

4. **Direct SQL is served through an authenticating proxy that holds the privileged principal; the kernel presents only its per-session capability.** For the authoring direct-database path of ADR-0011 decision 6, the kernel connects not to the database but to a trusted SQL proxy, presenting the same kind of per-session capability as decision 3. The proxy — trusted code — holds the privileged credential and binds each kernel connection to the single-tenant, read-only role for that session (the SQL analogue of how the API gateway scopes a request, ADR-0003). The database password lives only in the proxy; the "may act as any tenant" privilege lives only in the proxy; the kernel holds neither.

5. **Capabilities are minimal and non-escalatable.** A minted capability carries exactly the tenant, the identity, the single data plane it is for (API or SQL proxy), and read-only intent. It cannot be presented to a different service, broadened to another tenant, or exchanged for a longer-lived or higher-privilege credential. Possession of one capability never yields another.

6. **Revocation is central and immediate; lifetime is the backstop.** Ending a session invalidates its capabilities at the point that honours them — the API and the SQL proxy reject a revoked capability — without waiting for the short lifetime to lapse. The lifetime bounds the damage if central revocation is somehow missed; it is not the primary control.

## Alternatives considered

**A. Mint a real, short-lived, single-tenant database password into the kernel (a credential broker, possibly backed by a dynamic-secrets engine).** A broker issues the kernel a genuine database credential, scoped read-only to one tenant schema and time-bounded; the kernel connects to the database directly. *Rejected:* this places a real database password — a durable, broadly usable, slowly-revoked secret — inside user-controlled code, which decision 2 forbids. Doing it *well* requires either adopting a new dynamic-secrets dependency the platform does not have, or building bespoke per-session role lifecycle (create, rotate, drop) that is race-prone and easy to leak roles from. The proxy of decision 4 reaches the same single-tenant, read-only scoping with no database secret in untrusted space and with central, immediate revocation. (It is a legitimate, lower-component-count choice in a deployment that *already* runs a dynamic-secrets engine; it is not chosen as the platform's baseline.)

**B. A long-lived per-tenant credential shared by all of a tenant's notebook environments.** *Rejected:* a standing, shared secret in untrusted code is the opposite of every driver here — no per-user attribution, no meaningful revocation short of rotating a credential many environments depend on, and exactly the shared-credential exposure ADR-0011 decision 2 exists to prevent.

**C. Reuse the user's actual platform session (cookie/JWT) inside the kernel.** *Rejected:* the session is a long-lived, refreshable, full-capability browser credential delivered as an httpOnly cookie precisely so that code cannot read it (ADR-0004). Copying it into a server-side environment running user code would hand the kernel the user's entire authenticated surface rather than a minimal read-only data capability, and would make "revoke the notebook" mean "kill the user's web session." The minted, narrowly-scoped capability of decision 3 is the deliberate opposite.

**D. Serve all data over an HTTP query service and have no direct-SQL path at all.** A trusted service executes read-only queries and streams results, so no database-facing credential of any kind reaches the kernel. *Rejected as a replacement:* ADR-0011 decision 6 already decided that authors get direct database access through a real single-tenant principal, and a query-over-HTTP service does not satisfy "direct SQL" for the tools data scientists use. It remains fully compatible as an *additional* convenience and a possible future path, but it does not discharge the decision this ADR resolves.

## Consequences

### Positive

- ADR-0011's invariants become literally true rather than aspirational: no shared, standing, or tenant-crossing secret reaches the kernel, and — beyond what ADR-0011 stated — no raw database password reaches it either (decisions 1, 2, 4).
- The direct-SQL path reuses the platform's existing trusted-broker shape (the API gateway scoping per request), so the privileged database principal stays in trusted code exactly as it does everywhere else (decision 4).
- Revocation is a first-class control: a compromised or runaway environment is cut off centrally and immediately, not after a credential lifetime elapses (decision 6).
- A leaked capability is worth little — it is single-tenant, read-only, non-transferable to other services, unusable from outside the egress allowlist (ADR-0011 decision 7), and grants only data the user already may read (decisions 3, 5).
- One minting authority (the spawner) means there is a single place to reason about, audit, and constrain what any environment can ever hold (decision 1).

### Negative

- An authenticating SQL proxy is a new trusted component on the data path: it holds a privileged principal, must be hardened and kept available, and adds a hop and operational surface that a direct database connection would not have (decision 4).
- Two capability-honouring points — the data API and the SQL proxy — must both validate and both promptly honour revocation, so the revocation mechanism has more than one place to be correct (decision 6).
- Per-session minting adds issuance and lifecycle machinery to the spawn path that a static, pre-provisioned credential would not need (decision 1).
- The platform forgoes the simplicity of a dynamic-secrets broker (alternative A) that some teams already operate, accepting a bespoke proxy instead to avoid a new dependency and to keep database passwords out of untrusted code — a deliberate trade of one kind of component for another.

## Deferred decisions

- **Capability format and issuance internals.** Whether the minted capabilities are signed tokens, opaque references checked against a store, or another form, and how they are signed/validated, is an implementation concern owned by the relevant service, not this ADR.
- **SQL proxy selection and the tenant-binding mechanism.** Whether the proxy is an existing connection pooler extended with per-session role binding or a purpose-built component, and exactly how it assumes the single-tenant role per connection, is left to implementation within the property fixed by decision 4.
- **Lifetimes and revocation transport.** Concrete capability lifetimes and the precise mechanism by which a session ending propagates revocation to the API and the proxy are operational values owned by configuration and the implementing services.
- **Audit of credential use.** What record is kept of which capability accessed which tenant's data, and where, is desirable and unspecified here; it relates to the notebook-execution audit trail already deferred in ADR-0011.
- **A future query-over-HTTP data service.** Whether to also offer the result-streaming service of alternative D as an additional convenience is a separate decision, not foreclosed by this one.

## References

- ADR-0011 — embedded notebooks; this ADR resolves its deferred "short-lived single-tenant credential delivery" decision.
- ADR-0003 — tenant→schema resolution and the trusted-code scoping the SQL proxy mirrors.
- ADR-0004 — the platform session and transport from which per-session capabilities are minted and which they must not impersonate.
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
