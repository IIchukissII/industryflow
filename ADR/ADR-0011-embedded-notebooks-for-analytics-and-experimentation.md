<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0011: Embedded notebooks for analytics and experimentation

- **ID:** ADR-0011
- **Status:** Accepted
- **Date:** 2026-06-27
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0003 (tenant→schema resolution), ADR-0004 (API authentication, sessions and transport), ADR-0009 (Kubernetes deployment and packaging)
- **Refined by:** ADR-0018 (spawner portability — decision 1's "per-user pod spawner" is realized as KubeSpawner on Kubernetes and DockerSpawner on Compose, so the per-user environment is a *pod or a container*; the isolation properties here are unchanged and binding under both); ADR-0015 (the DB principal of decision 6 — "short-lived, per-session, not shared" — is refined into a *standing* per-tenant `NOLOGIN` role assumed via the SQL proxy, with the short-lived/per-session property moved onto the capability handle; the end invariants (single-tenant, read-only, revocable, no shared secret in the kernel) are preserved)
- **Related:** ADR-0002 (least-privilege, network-boundary posture for an untrusted-input service)

## Context and problem

IndustryFlow needs in-product data analytics and experimentation: operators want to explore a tenant's telemetry and read curated reports, and data scientists want to write code, query data, and train models against a tenant's own history. Today the only notebook capability is a standalone Jupyter container that runs JupyterLab with authentication disabled, shared across everyone, with the same database and object-store credentials as the ML service baked into its environment. It is a data-scientist sidecar, not a product surface — and it cannot become one as built: it has no per-user identity, no tenant scoping, and it hands arbitrary user code a credential that can read every tenant's data.

That last point is the crux, and it is specific to how this platform isolates tenants. Tenant data is separated by schema with no row-level security (ADR-0003): isolation is produced by *trusted service code* setting `search_path` to the caller's tenant schema and nothing else. Every service that touches tenant data is trusted code reviewed to do exactly that. A notebook kernel breaks the assumption the whole model rests on, because the code in a kernel is written by the user, not by us. A kernel that holds a shared service role can simply set its search path to another tenant's schema and read it; the isolation is enforced by discipline the kernel does not have to follow. This is the same failure class the platform has been removing elsewhere — duplicated tenant-resolution discipline (ADR-0000), an internet-facing service holding a database role it did not need (ADR-0002) — now in its most dangerous form, because the untrusted party is executing arbitrary code rather than sending a request a trusted service validates.

So embedding notebooks is not primarily a UI problem. It is the problem of admitting user-authored code into a multi-tenant system whose tenant isolation was designed on the premise that only trusted code runs server-side. The decision this ADR records is how analytics and experimentation are delivered such that admitting that code does not weaken the boundary the rest of the architecture depends on.

## Decision drivers

- **A kernel runs untrusted code, so isolation cannot depend on the kernel's good behaviour.** Under schema-per-tenant with no RLS (ADR-0003), any isolation that relies on the executing code setting `search_path` correctly is no isolation at all once that code is user-authored. The boundary must be enforced by something the kernel cannot talk its way past.
- **Least privilege and explicit network boundaries are the platform's established posture.** Per-service database roles, mTLS-only ingestion, and egress NetworkPolicies (ADR-0002, ADR-0009) are how trust is contained here. A notebook environment must fit that posture, not carve an exception into it.
- **Operators and data scientists carry very different risk.** Reading curated charts is low risk; executing arbitrary Python with data access is the highest-risk capability the product could expose. Forcing both through one mechanism would either over-restrict analytics or under-contain experimentation.
- **Reuse the existing identity, session, and edge.** The platform already issues a session-scoped identity carrying the tenant and role (ADR-0004) behind a single front-door origin. A notebook surface should authenticate from that identity rather than introduce a second login or a second public authentication surface to attack.
- **Decisions are recorded before they are built (ADR-0000).** This is a cross-cutting decision over tenancy, auth, and deployment; it is captured here so the implementation in compose, Helm, SQL grants, and the frontend has one rationale to conform to rather than several re-derived ones.

## Decision

1. **Analytics and experimentation are delivered as server-side, per-user, isolated notebook environments, embedded in the product.** Each user who opens a notebook gets their own ephemeral, resource-limited execution environment, spawned on demand and reclaimed when idle, rather than sharing one long-lived server. A managed multi-user notebook backend (a JupyterHub-style hub with a per-user pod spawner) is the mechanism; the decision here is the *property* — one isolated environment per user, never a shared one — not any particular product's feature set.

2. **The kernel is untrusted and holds no ambient, tenant-crossing credential.** No notebook environment is given a shared service database role, the object store's root credentials, or any secret whose scope exceeds the single tenant and identity that opened it. Whatever a kernel can reach, it can reach only for its own tenant. This is the non-negotiable invariant the rest of the decisions serve.

3. **Tenant isolation for notebooks is enforced by the database, not by `search_path` discipline.** Because the executing code is user-authored, the schema boundary that trusted services maintain by convention must, for notebooks, be enforced by privilege: a notebook's database principal is granted access to exactly one tenant's schema and cannot select from any other, so a cross-tenant query *fails* rather than relying on the code not to attempt it. This is the deliberate exception to ADR-0003's trusted-code model, made because the precondition of that model — that only trusted code runs — does not hold for a kernel.

4. **The default and lowest-privilege data path is a tenant-scoped data API called with the user's own identity.** A notebook obtains data through a platform-provided client that calls the existing API surface authenticated as the user, so the same per-tenant authorization that governs the rest of the product (ADR-0004) governs notebook data access, with no database credential in the kernel at all. This is the only data path for the read-only profile and the default for all profiles.

5. **Two role-driven profiles share one isolation primitive.** The session identity's role (ADR-0004) selects the environment a user receives: a *read-only analytics* profile for operators — rendered reports and dashboards, parameters but no free-form code, data via the API path of decision 4 only — and an *authoring* profile for data scientists — a full interactive environment that may, in addition, receive direct data access under decision 6. Both are spawned by the same backend with the same isolation; they differ in capability and limits, not in mechanism.

6. **Direct database access for the authoring profile is a short-lived, read-only, single-tenant principal — never a shared service role.** When an authoring environment needs to query data directly (rather than through the API), it is given a credential whose grants reach exactly the one tenant schema of the user who opened it, which is read-only, and which is short-lived rather than standing. It is provisioned per session, not shared, and it is distinct from every service role the platform already defines. The mechanism by which such a credential is delivered without placing a reusable secret inside user-controlled code is deferred (see Deferred decisions), but the properties — single-tenant, read-only, short-lived, non-shared — are decided here.

7. **Notebook environments are network- and resource-contained, consistent with the platform's deployment posture.** Each environment runs with an egress allowlist permitting only the platform endpoints it legitimately needs and denying lateral reach to other services and tenants, with CPU/memory limits, with ephemerality by default, and under per-tenant concurrency and quota caps. This applies ADR-0002's and ADR-0009's containment posture to the highest-privilege surface the product exposes.

8. **The notebook surface is embedded behind the existing front door and authenticates from the existing session — it introduces no second login and no new public authentication surface.** The hub trusts the platform's established session identity (ADR-0004) rather than maintaining its own user store, and it is reached through the same single origin and edge as the rest of the product. A user who is logged in does not log in again, and there is no separately exposed notebook authentication endpoint to harden in isolation.

9. **Experiment tracking from notebooks is gated until tracking-store multi-tenancy is decided.** The experiment-tracking store is single-tenant today: its experiments, runs, and artifacts are not isolated per tenant. Until that is resolved (deferred below), notebook access to it is mediated so that it cannot become a cross-tenant disclosure path, and broad authoring write-access to it waits on that design rather than shipping ahead of it.

## Alternatives considered

**A. Expose the existing shared Jupyter server through the product.** *Rejected:* it has no per-user identity, no tenant scoping, authentication disabled, and a shared data credential — the precise combination decision 2 forbids. Exposing it would hand every user arbitrary code execution with access to every tenant's data. It remains acceptable only as what it is today: an out-of-band developer tool, not a product surface.

**B. In-browser execution only (a WASM/Pyodide notebook), with no server-side kernel.** *Rejected as the sole solution:* running the kernel in the user's browser gives automatic tenant isolation — the code has only the user's own session to call APIs with — and is genuinely attractive for light, read-only analytics. But it cannot serve the authoring audience: it has no access to server-side compute, the full library ecosystem, or datasets too large to ship to a browser, and model training is out of reach. It is retained as a possible future implementation of the read-only profile (decision 5), not as the answer for experimentation.

**C. Give the kernel the existing ML-service database role and rely on `search_path` discipline.** *Rejected:* this is the status quo credential model applied to user code, and it is exactly the cross-tenant breach decision 3 exists to prevent. Isolation by discipline works only for trusted code; a kernel is not trusted code. Choosing this would re-introduce, in the most exploitable place, the isolation-by-convention fragility the platform has been retiring.

**D. Build a bespoke notebook UI in the frontend over raw kernel protocols.** *Rejected for now:* driving kernels directly from a custom UI would avoid embedding a third-party notebook application, but it reinvents a mature, complex piece of software (the notebook front-end) at high cost and with its own large attack surface, while still needing every isolation decision above. Embedding the established notebook UI behind the existing edge reaches the same capability for far less, and the isolation work — not the UI — is where the risk and the value are.

**E. Per-tenant standalone notebook deployments managed outside the platform.** *Rejected:* one full notebook deployment per tenant would isolate tenants by brute force but carries a per-tenant operational burden, no single-sign-on from the platform session, and a standing fleet of long-lived servers that contradicts the ephemeral, on-demand, quota-bounded posture of decision 7. It also drifts from the product rather than living inside it.

## Consequences

### Positive

- The platform can offer in-product analytics and experimentation without weakening the tenant boundary, because the boundary for notebooks is enforced by database privilege (decision 3) rather than by trust the kernel would have to honour.
- The highest-privilege surface the product exposes inherits the platform's existing containment posture — least privilege, egress allowlists, resource and concurrency limits (decision 7) — instead of becoming an exception to it.
- Operators and data scientists are served by capability-appropriate profiles over one isolation primitive (decision 5), so analytics is not over-restricted and experimentation is not under-contained.
- No second login and no new public authentication surface are introduced; the notebook surface reuses the session, role, and edge already designed and hardened (decisions 4, 8).
- A clean default data path — the tenant-scoped API called as the user (decision 4) — means most notebook usage needs no database credential in the kernel at all, shrinking what decision 6 has to secure.

### Negative

- A new, genuinely high-risk capability — arbitrary server-side code execution — enters the product, and it must be operated as such: spawner, per-user environments, images, and quotas are real surface and real cost that did not exist before.
- Enforcing tenant isolation by database privilege for notebooks (decision 3) requires per-tenant, read-only principals beyond the per-service roles already defined, and a way to deliver short-lived credentials into untrusted environments (decision 6) that is the hardest single piece of this design and is deliberately left to a follow-up.
- The deliberate exception to ADR-0003's trusted-code isolation model (decision 3) means there are now two isolation mechanisms — trusted-code `search_path` for services, privilege-enforced grants for notebooks — and the distinction must be understood and maintained rather than collapsed.
- Experiment tracking, which data scientists will reasonably expect to use freely, is gated (decision 9) until a separate multi-tenancy decision lands, so the authoring profile is not fully featured at first.
- On-demand per-user environments add scheduling, image-management, idle-reclamation, and quota concerns to operations that a static service set does not have.

## Deferred decisions

- **Short-lived single-tenant credential delivery.** How the read-only, per-tenant, short-lived database principal of decision 6 is delivered to an authoring environment without placing a reusable secret inside user-controlled code — a broker that mints time-bounded credentials versus an authenticating connection proxy that maps the session identity to the tenant principal so no password reaches the kernel — is a security-critical sub-decision deferred to its own record.
- **Experiment-tracking multi-tenancy.** Isolating the tracking store's experiments, runs, and artifacts per tenant (decision 9) — namespaced experiments behind an access-filtering proxy versus a per-tenant tracking instance — is its own decision and must land before authoring write-access to tracking goes wide.
- **Notebook and package supply chain.** The base image contents, how (and whether) data scientists may install additional packages within an environment, and how curated operator notebooks are published and promoted are operational policies left to implementation.
- **Notebook persistence, versioning, and audit.** Where authored notebooks live, how they are versioned, and what record is kept of who executed what against which tenant are deferred; an execution audit trail is desirable but unspecified here.
- **Quota and cost model.** Concrete per-tenant concurrency caps, resource limits, and idle-reclamation timing (decision 7) are values owned by the deployment configuration, not this ADR.

## References

- ADR-0003 — tenant-to-schema resolution and the trusted-code `search_path` isolation model this ADR deliberately excepts for notebooks.
- ADR-0004 — the session identity, role claim, and single-origin transport the notebook surface authenticates from.
- ADR-0009 — the Kubernetes deployment and packaging posture the per-user environments and their network/resource containment fit into.
- ADR-0002 — the least-privilege and network-boundary precedent for containing a surface that handles untrusted input.
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
