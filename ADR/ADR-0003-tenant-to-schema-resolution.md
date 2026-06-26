<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0003: Tenant-to-schema resolution — verified identity, validated mapping, one implementation

- **ID:** ADR-0003
- **Status:** Accepted
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0002 (ingestion authentication & device identity), ADR-0004 (API authentication & CORS)

## Context and problem

IndustryFlow isolates tenants by schema: each tenant's data lives in a `tenant_<id>` PostgreSQL schema, and a request is routed to its tenant by setting the connection's `search_path` before queries run. The schema-per-tenant model itself is sound and is not in question here. *How a request decides which schema it belongs to* is the problem, and the current mechanism is wrong in three independent ways, each found in the review and each reproduced across several services:

- **The tenant is discovered by scanning, not known.** Every authenticated request lists all `tenant_%` schemas and, for each, issues `SET search_path` plus `SELECT company_id FROM "user"` until one matches (e.g. `services/ingestion_service/dependencies.py:53-73`, and the same shape copy-pasted ~4× in ml_service). This is an O(number-of-tenants) walk on the hot path, holding a pooled connection the whole time — review finding **X2**.
- **The schema name is built from unvalidated input and spliced into SQL.** `company_id` is turned into a schema name by `replace('-','_')` only — never validated as a UUID — and f-string-interpolated into `SET search_path TO {schema}`. Because asyncpg's simple-query protocol allows stacked statements, a crafted `company_id` (reachable from a request body in ml_service, and from untrusted Kafka payloads in the alert worker) is a SQL-injection vector — review finding **X1**.
- **The tenant's `search_path` leaks back to the pool.** After a request finishes, the connection returns to the pool still carrying the last tenant's `search_path`; nothing resets it. It is masked today only because each method re-issues `SET search_path` first — a fragile invariant one forgotten call away from cross-tenant data exposure — review finding **X4**.

These are three symptoms of one missing decision. There is no single, recorded answer to "given a request, what schema does it use, and how is that applied safely?" — so each service answered it independently, each answer carried the same defects, and fixing one copy fixes none of the others. ADR-0002 already removed the question for the ingestion path by sourcing the tenant from a verified client certificate; this ADR generalizes that principle to every path and fixes the mapping and connection-hygiene defects that ADR-0002 did not reach.

## Decision drivers

- **The tenant is a property of the authenticated principal, not a database lookup.** Once a request is authenticated, *who the tenant is* is already known from the credential. Re-discovering it by scanning schemas is both slow (X2) and an admission that the identity wasn't trusted to carry it.
- **A schema name is an SQL identifier and must be treated as one.** It can never be an unvalidated, caller-influenced string interpolated into a statement (X1). The only values that reach `search_path` must be ones the system constructed from a validated identity.
- **A pooled connection must carry no tenant state across requests.** Tenant isolation cannot depend on every future query remembering to re-set the path first (X4); the safe default must be enforced by construction.
- **One implementation, used everywhere.** A cross-cutting rule copy-pasted per service drifts and carries its bugs with it (the very failure ADR-0000 names). Resolution must have a single authoritative implementation that all services call.
- **Schema-per-tenant is the committed isolation model.** This ADR decides resolution *within* that model; it does not reopen the choice of isolation strategy (see Alternatives).

## Decision

### Where the tenant comes from

1. **The tenant identity is established once, from the authenticated principal, and never re-discovered by scanning.** For API (human/service) requests the tenant (`company_id`) is read from a signed JWT claim; for device ingestion it is read from the verified client certificate (ADR-0002). A request's tenant is determined before any tenant-scoped query runs, from the credential alone.

2. **The JWT carries the tenant as a signed claim, issued at login.** Authentication (ADR-0004) places `company_id` into the token's claims, so resolving the tenant on a request is *reading a verified claim*, not querying the database. Where a principal-to-tenant lookup is genuinely unavoidable, it is a single indexed query (by primary key), never a scan over schemas, and its result may be cached. The per-request `tenant_%` scan is removed (closes X2).

3. **The tenant is never taken from the request body or an unauthenticated header.** A `company_id` supplied in a payload is not a tenant identity; it is untrusted input. This forbids the ml_service pattern where the predict body's `company_id` selected the schema (closes the X1 source on that path).

### How the schema name is built and applied

4. **`company_id` is validated as a UUID before any schema name is constructed.** Resolution begins by parsing the identity's `company_id` as a UUID; a value that does not parse is rejected before it can influence SQL. Only the canonical string form of the parsed UUID is used downstream (closes X1 at the root: no caller-controlled string ever reaches the statement).

5. **There is one canonical `company_id → schema` function.** A single function maps a validated `company_id` to its `tenant_<id>` schema name. It is the only place the mapping exists; no service re-derives it inline. The schema name it returns is treated as a trusted identifier (constructed by the system, quoted when emitted), not as interpolated user input.

6. **`search_path` is scoped to the request and cannot outlive it.** The tenant `search_path` is applied with transaction-local scope (`SET LOCAL` inside the request's transaction) so it is automatically discarded when the transaction ends; where a transaction is not used, the path is explicitly reset in a `finally` before the connection returns to the pool. A pooled connection therefore never carries a previous tenant's `search_path` (closes X4), and tenant isolation no longer depends on every query re-setting the path first.

### One implementation

7. **Tenant resolution is a single shared implementation that every service uses.** The "obtain validated tenant identity → map to schema → apply transaction-scoped `search_path`" sequence lives in one authoritative place and is consumed by api_gateway, ml_service, alert_service, and ingestion_service alike. The copy-pasted per-service variants are removed. How the shared code is packaged across these separate deployment units (shared library vs. a single vetted, referenced helper) is an implementation concern noted under Deferred decisions.

## Alternatives considered

**A. Keep per-request scanning with f-string `search_path` (status quo).** *Rejected:* this is exactly the X1/X2/X4 cluster. Scanning is the wrong primitive when the identity already names the tenant; f-stringing an unvalidated identifier into SQL is the injection; leaving the path set is the leak.

**B. Cache the principal→tenant mapping but otherwise keep scanning and f-string interpolation.** *Rejected:* a cache hides X2's cost some of the time but leaves X1 and X4 fully intact, and it treats a database scan as the canonical resolution primitive rather than reading the identity the credential already carries. It mitigates a symptom and ignores the cause.

**C. Trust `company_id` supplied in the request body/header.** *Rejected:* this is the unauthenticated cross-tenant vector the review found in ml_service. A caller-asserted tenant is not an identity; decisions 1 and 3 exist specifically to forbid it.

**D. Replace schema-per-tenant with row-level security (a `tenant_id` column + Postgres RLS).** *Rejected (out of scope):* RLS is a different isolation model, not a resolution mechanism within the current one. Migrating to it is a large architectural change touching every table and query and is not what this ADR decides; it is recorded as a possible future direction (Deferred) but does not block fixing the present model. The committed model is schema-per-tenant.

**E. Database-per-tenant instead of schema-per-tenant.** *Rejected (out of scope):* stronger isolation at a much higher operational cost (connections, migrations, pooling per tenant). Like D, it reopens the isolation strategy rather than deciding resolution within it, and is out of scope here.

## Consequences

### Positive

- The X1 injection, the X2 per-request scan, and the X4 pooled-connection leak are closed together, because they were one missing decision and now have one answer.
- Tenant resolution becomes O(1) (read a claim / a cert field), removing a database walk from the highest-traffic paths.
- The tenant boundary is derived solely from an authenticated principal and a validated UUID, so no caller-supplied string can steer a query into another tenant's schema.
- Resolution has a single authoritative implementation, so the logic can no longer drift or carry its bugs across services — the ADR-0000 discipline applied to code.

### Negative

- The JWT now carries the tenant claim, so a change to a user's company requires re-issuing the token; this is rare and acceptable, but it couples tenant membership to token lifetime (ADR-0004's revocation/expiry decisions interact here).
- Adopting the shared resolution path means touching every service that currently scans — a migration with regression risk that must be done deliberately, not file-by-file in passing.
- Transaction-scoped `search_path` (`SET LOCAL`) requires tenant-scoped queries to run inside a transaction; code paths that issued ad-hoc statements outside a transaction must adopt that pattern.
- A shared implementation across separate deployment units needs a packaging answer (library or vetted shared module); until that exists, "one implementation" is a discipline the review must enforce rather than something the build guarantees.

## Deferred decisions

- **Shared-code packaging.** Whether the single resolution implementation ships as an internal shared library, a vendored module, or another mechanism — given the services are independent deployment units without a shared package today — is an implementation decision left open.
- **Caching of any residual principal→tenant lookup.** If a lookup remains for some principals, its cache scope, TTL, and invalidation are unspecified here.
- **Long-term isolation strategy.** Whether IndustryFlow eventually moves from schema-per-tenant to RLS or database-per-tenant (alternatives D/E) is a separate, larger decision for a future ADR if schema-per-tenant scaling or operational cost forces it.
- **Defense-in-depth schema qualification.** Whether tenant tables are additionally schema-qualified in queries (belt-and-braces over `search_path`) is left to implementation guidance.

## References

- IndustryFlow review (2026-06-26), findings X1 (`search_path` injection), X2 (per-request tenant-schema scan), X4 (pooled-connection `search_path` leak) — internal report.
- ADR-0002 — ingestion authentication & device identity; sources the verified tenant identity for the ingestion path that this ADR generalizes.
- ADR-0004 — API authentication & CORS; owns the JWT claim that decision 2 relies on.
