<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0019 — Notebook experiment-tracking gateway and the tracking capability

- **Status:** Accepted
- **Date:** 2026-06-28; changed 2026-10-27
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md)
- **Related:** [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) (the
  gateway this makes concrete), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md)
  (handles and audiences), [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md)
  (the tracking gate), [ADR-0014](ADR-0014-notebook-hub-single-sign-on.md) (hub identity),
  [ADR-0039](ADR-0039-one-tenant-ownership-test.md) (the one implementation of decision 5's test)

## Context

ADR-0013 decides that tracking and the model registry are made multi-tenant by a trusted gateway and
leaves the mechanism open. The tracking server is one shared, unmodified server over one metadata
database and one bucket; its own authorization is not tenant-aware, and its credentials must stay out
of the kernel. Artifacts are large, metadata calls small.

## Decision

1. **The kernel reaches tracking only through a trusted gateway, with a per-session tracking
   capability.** Store credentials live only in the gateway; trusted platform code may still address
   the tracking server directly (decision 7).
2. **Tracking is a distinct capability audience.** A tracking handle yields no SQL or API access, and
   neither yields tracking (ADR-0015 dec 1-3).
3. **The spawner mints the tracking handle, for the authoring profile only.** The kernel cannot mint,
   refresh or widen it.
4. **The kernel presents the capability as the tracking client's bearer token.** The gateway resolves
   it in the capability store, the lookup revocation empties, and proxies with its own principal.
5. **The gateway forces every tracking, registry and artifact operation into the caller's tenant
   namespace and refuses any that would cross it.** Names carry a prefix built from the tenant's
   immutable identifier, stripped on read, and artifacts sit under a per-tenant object-store prefix.
6. **Artifact bytes move directly between kernel and object store through gateway-minted pre-signed
   addresses, each for one object, one verb and a few minutes.** Listing and deleting move no bytes
   and are done by the gateway; the deployed object store pre-signs, so no byte-proxying path exists.
7. **The tracking server is unmodified and its UI stays internal.** Tenants browse models, experiments
   and runs through the platform's session-scoped read paths, which apply decision 5's rule through
   the same shared implementation as the gateway (ADR-0039).
8. **The capability store holds tracking handles, never store secrets.**
9. **ADR-0011's tracking gate is lifted for the authoring profile.** An authoring kernel logs runs,
   reads its runs and registers model versions within its tenant only.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Expose the server with its own authorization | Not tenant-aware; ADR-0013 dec 1 alternatives |
| 1 | Shared credentials, tenants by naming convention | Broad credentials in the kernel; ADR-0013 dec 1 alternatives |
| 2 | Reuse the SQL or API capability | Audiences are non-interchangeable |
| 5 | A server, database and bucket per tenant now | Fleet cost before need; ADR-0013 dec 5 alternatives, kept as later hardening |
| 5 | A client library that scopes itself | Scoping in untrusted, bypassable code |
| 5 | A human-readable tenant slug in the namespace | Neither unique nor stable across a rename, so no security boundary |
| 6 | Prefix-scoped object-store session credentials | Reusable over every object in the prefix; broader than decision 6 |
| 6 | Proxy all artifact bytes through the gateway, as default or fallback | Puts the gateway in the data path, and the deployed object store pre-signs |
| 7 | Tenant browsing by a tenant schema search path on the tracking server's shared database | A no-op that returns every tenant's experiments and models |

## Consequences

**Positive**
- Tracking credentials leave untrusted environments; every access is tenant-scoped through the
  platform's one mediation pattern.

**Negative**
- The tracking tenant boundary rests on the gateway, and on the read paths, being correct.
- A pre-signed address cannot be revoked before it expires.
- Tracking data from before the namespace is outside every tenant until attributed or retired.

## Deferred

| Open question | Settled by |
|---|---|
| A byte-proxying fallback | First supported object store that cannot pre-sign per object |
| A per-tenant artifact bucket on top of the prefix | ADR-0013 dec 5's move to physical separation |
| Attribution or retirement of pre-namespace data | Operator task (ADR-0013) |
| Per-tenant retention and quota | Deployment configuration (ADR-0013) |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): namespace literals, routes.
- `services/notebook_tracking_gateway/`; read paths in `services/ml_service/api/routers/`.
