<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0011 — Embedded notebooks for analytics and experimentation

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-29
- **Parent:** ADR-0001
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (the trusted-code isolation this record excepts), [ADR-0012](ADR-0012-notebook-credential-delivery.md) and [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (credential delivery, SQL proxy), [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) and [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (tracking), [ADR-0014](ADR-0014-notebook-hub-single-sign-on.md) (hub sign-on), [ADR-0018](ADR-0018-notebook-hub-spawner-portability.md) (spawner portability), [ADR-0020](ADR-0020-notebook-per-user-persistence.md) (persistence), [ADR-0042](ADR-0042-capability-use-audit.md) (the capability-use record)

## Context

Operators want to read a tenant's telemetry and curated reports; data scientists want to write code,
query data and train models against a tenant's history. Running arbitrary code with data access is the
highest-risk capability the product can expose. Tenant data is separated by schema without row-level security; trusted service code scopes each
session to the caller's tenant (ADR-0003). A kernel runs user-authored code, so a kernel holding a shared
service role could re-scope itself to another tenant.

## Decision

1. **Analytics and experimentation run in server-side, per-user, isolated notebook environments,
   embedded in the product.** Each user gets an ephemeral, resource-limited environment, spawned on
   demand and reclaimed when idle, never shared.
2. **The kernel is untrusted and holds no ambient, tenant-crossing credential.** No environment holds a
   shared service role, object-store root credentials, or any secret wider than its one tenant and
   identity.
3. **Notebook tenant isolation is enforced by database privilege, not by search-path discipline.** A
   notebook's database principal is granted exactly one tenant's schema, so a cross-tenant query fails;
   this is the deliberate exception to ADR-0003's trusted-code model.
4. **The default data path is the tenant-scoped data API, called as the user.** It is the only data path
   of the read-only profile and needs no database credential in the kernel.
5. **Two role-driven profiles share one isolation primitive.** The session role selects *read-only
   analytics* (rendered reports, parameters, no free-form code, data API only) or *authoring* (a full
   environment that may also use decisions 6 and 9); a role not mapped to authoring gets read-only.
6. **Authoring's direct database access reaches one tenant, read-only, through a principal the kernel
   never holds.** The principal is standing, per tenant and held only by trusted mediation; the kernel
   holds a per-session, short-lived, revocable capability that reaches it only through the mediator.
7. **Notebook environments are network- and resource-contained.** Each has an egress allowlist of the
   platform endpoints it needs, CPU and memory limits, ephemerality by default, and per-tenant
   concurrency and quota caps.
8. **Notebooks sit behind the existing front door and authenticate from the existing session.** There
   is no second login and no separately exposed notebook authentication endpoint.
9. **Experiment tracking from notebooks goes only through a trusted gateway that confines it to the
   caller's tenant.** The environment uses a capability of its own and never holds a tracking-store or
   object-store credential.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | In-browser execution only | No server compute, full library stack or large data; kept as a possible read-only implementation |
| 1 | A bespoke notebook UI over raw kernel protocols | Reinvents a mature front end and still needs the same isolation work |
| 1 | A notebook deployment per tenant | Per-tenant operations, no platform sign-on, and standing fleets against on-demand containment |
| 2 | A shared notebook server exposed through the product | No per-user identity, no tenant scope, and a shared credential |
| 3 | The ML-service role in the kernel, scoped by search path | Isolation by discipline does not hold for user-authored code |
| 6 | A database principal created and dropped per session | Race-prone role lifecycle, and its credential would sit in the kernel |
| 9 | Notebook experiment tracking withheld until tracking-store tenancy is decided | A gateway that confines tracking to the caller's tenant now serves it without that wait |

## Consequences

**Positive**
- Notebooks do not weaken the tenant boundary, because they are isolated by privilege.

**Negative**
- Two isolation mechanisms coexist, trusted-code scoping for services and privilege for notebooks.
- Per-tenant concurrency and quota caps (decision 7) are not built; per-environment limits are.
- The read-only profile's interface is not locked to rendering; its reach is the read-only data API.

## Deferred

| Open question | Settled by |
|---|---|
| Image contents, in-environment package installation, publication of curated notebooks | Maintainer, before curated content ships |
| Notebook versioning and a record of which code a kernel ran | ADR-0042's deferral |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): components, profiles, request path.
