<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Embedded notebooks (analytics & experimentation)

Design overview; see the ADRs for the decisions:
[ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (shape),
[ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md) (credentials),
[ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) (experiment tracking),
[ADR-0014](../../ADR/ADR-0014-notebook-hub-single-sign-on.md) (SSO), and
[ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) (capability minting + the SQL proxy).

In-product analytics (for operators) and experimentation (for data scientists) are delivered as
per-user, isolated notebook environments embedded in the frontend. Because a kernel runs
**user-authored code**, it is treated as untrusted: it holds no shared or ambient credential and
never a database password, and tenant isolation for it is enforced by **database privilege**, not
by the trusted-code `search_path` discipline used elsewhere (ADR-0003).

## How it fits together

```
browser ─▶ SSO proxy ──auth_request──▶ api-gateway GET /auth/verify   (validate session)
   (logged-in)  │ forwards verified X-IF-* identity (overwrites client-supplied)
                ▼
          notebook hub ──spawns──▶ per-user pod (role-chosen profile, tenant-bound,
                │ mints per-session capabilities                 network-restricted)
                ▼
   kernel holds opaque capability handles only ─▶ data API (X-IF-Capability, read-only)
                                                └▶ SQL proxy (SET ROLE tenant_reader_<uuid>)
```

The kernel's only credentials are opaque, single-tenant, read-only, revocable capability
handles minted by the hub at spawn (ADR-0012/0015) — never a session token, DB password, or
object-store secret.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Per-tenant read-only DB role + tenant-scoped data path | **done** (DB-proven in CI) |
| 2 | Hub SSO + per-user spawner logic + runtime manifests | **built; runtime not cluster-validated** |
| 3 | Operator read-only surface (rendered dashboards) | planned |
| 4 | Capability minting + the SQL access proxy | **logic built & tested; wire cluster-bound** |
| 5 | Experiment-tracking gateway (ADR-0013) | planned |

The design is decision-complete (ADR-0011→0015). Every cluster-independent piece is built and
covered by tests; what remains needs a running cluster to validate.

## Built and tested in-process

- **Per-tenant read-only role.** `create_tenant_schema()` provisions a `NOLOGIN`,
  read-only, single-schema `tenant_reader_<uuid>` (USAGE + SELECT on current and future tables;
  TimescaleDB propagates SELECT to hypertable chunks). A cross-tenant query as this role **fails
  at the GRANT level**. Existing tenants are backfilled by
  `infrastructure/timescaledb/init-scripts/09-tenant-reader-roles-migration.sql`, and the
  boundary is **proven against a real database** by `test_tenant_reader_isolation.py` in the
  `db-tenant-isolation` CI workflow.
- **Tenant-scoped data API** (`api_gateway`). `GET /api/measurements` (+ `/{sensor_id}`),
  `GET /api/aggregations/{window}` (with `start`/`end`/`order`, capped at 1000 rows), and
  `GET /api/training-data/equipment/{id}` (+ `/stream`, bulk DataFrame pulls). Each resolves
  **either** the platform session **or** an `X-IF-Capability` handle to the caller's tenant; a
  capability request runs **read-only**.
- **SSO handoff.** `GET /auth/verify` validates the session with the one existing verification
  and returns the identity in `X-IF-*` headers (ADR-0014); the SSO reverse proxy consumes it.
- **Spawner logic** (`services/notebook_hub/identity.py`): parse the verified identity, choose
  the profile from the role, bind the pod to its tenant with identity-only environment.
- **Capability mint/resolve/revoke** (`services/notebook_hub/capabilities.py` + the
  verifier-side `capability_auth.py` in api_gateway and `binding.py` in the SQL proxy): opaque
  handles backed by a short-lived store entry, single-tenant, read-only, audience-bound
  (API vs SQL, not interchangeable), revoked by deleting the entry.
- **SQL proxy policy + orchestration** (`services/notebook_sql_proxy`): resolve an SQL handle →
  tenant, `SET ROLE` into the reader role, relay; a denied handle opens no backend.
- **Client** (`clients/python/industryflow`): loads tenant data into pandas DataFrames, sending
  the handle in `X-IF-Capability`; holds no DB/object-store credential.

## Cluster-bound (not yet validated)

- The **hub runtime** in the Helm chart behind `notebookHub.enabled` (hub + RBAC, the
  configurable-http-proxy, the SSO reverse proxy, the single-user egress NetworkPolicy, the
  Ingress) — these **render and lint** in CI but have not run on a cluster.
- The **SQL proxy's Postgres-wire backend** (read the handle from the startup message, hold the
  privileged principal, relay bytes).
- The **JupyterHub config wiring**, an end-to-end spawn, and the **notebook images**.

## Deferred to their own work

The experiment-tracking gateway (ADR-0013) and the frontend `/analytics` and `/experiments`
pages. Experiment tracking from notebooks remains gated until that gateway lands.
