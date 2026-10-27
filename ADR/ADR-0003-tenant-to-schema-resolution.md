<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0003 — Tenant-to-schema resolution: verified identity, validated mapping, one implementation

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-27
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md)
- **Related:** [ADR-0002](ADR-0002-ingestion-authentication-and-device-identity.md) (verified tenant
  on ingestion), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (the tenant claim),
  [ADR-0039](ADR-0039-one-tenant-ownership-test.md) (the ownership test the library holds)

## Context

Each tenant's data lives in its own PostgreSQL schema, reached by setting the connection's search
path. A schema name is an SQL identifier, so caller-influenced input reaching it is an injection
vector; pooled connections outlive requests; a rule implemented per service drifts.

## Decision

1. **The tenant is established once, from the authenticated principal, never by scanning.** API
   requests take it from the authenticated user, device ingestion from the verified certificate.
2. **The access token carries the tenant as a signed claim.** An unavoidable principal-to-tenant
   lookup is one primary-key query.
3. **The tenant is never taken from a request body or an unauthenticated header.**
4. **The tenant identifier is validated as a UUID before any name is derived from it.** Only the
   canonical form of the parsed UUID is used downstream.
5. **One canonical function maps a validated tenant identifier to its schema name.**
6. **The search path is scoped to the request's transaction.** It is discarded when the transaction
   ends, so tenant-scoped queries run inside one and no pooled connection keeps a tenant's path.
7. **Tenant resolution is one shared library every deployable is built with.** It holds every name
   derived from a tenant identifier (schema, roles, registry and object-store namespaces) and the
   test of whether a name or key belongs to a tenant (ADR-0039); a check refuses a service that
   derives a name itself, a build that omits the library, and a database copy of the names that
   differs.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| — | Row-level security over shared tables | A different isolation model, not resolution within the settled one |
| — | A database per tenant | Stronger isolation at a per-tenant cost in connections, migrations and pooling; reopens the model |
| 1 | Scanning tenant schemas per request until the principal matches | A per-request cost that grows with tenants, holding a pooled connection throughout |
| 2 | Caching the principal-to-tenant mapping while still scanning | Hides the cost some of the time and keeps the scan as the resolution primitive |
| 3 | A tenant taken from the request body or a header | A caller can steer a query into another tenant's schema |
| 4 | A schema name built by string substitution without validation | A caller-influenced string reaches an SQL statement |
| 6 | A session-wide search path re-set before each query | The pooled connection keeps the last tenant's path, one forgotten call from cross-tenant exposure |
| 7 | Resolution implemented separately in each service | Copies drift and carry the same defects |
| 7 | One vetted helper copied into each deployable, held by review | Review, not the build, would guarantee the single implementation |

## Consequences

**Positive**
- No caller-supplied string can steer a query into another tenant's schema.
- Resolution is cheap, and the build guarantees one implementation.

**Negative**
- A change to a user's tenant requires a new token (ADR-0004).
- Every deployable carries the library; the database's copy of the naming rules relies on the check.

## Deferred

| Open question | Settled by |
|---|---|
| Caching of the residual principal-to-tenant lookup | Measured load on that lookup |
| Moving to row-level security or database-per-tenant | A future ADR, if schema-per-tenant cost forces it |
| Schema-qualifying tenant tables in addition to the search path | Implementation guidance |

## References

- `libs/industryflow_contracts/industryflow_contracts/tenancy.py`: the shared implementation.
- `tests/config_guards/test_tenant_contract_copies.py`, `test_images_carry_contracts.py`: the checks
  of decision 7.
