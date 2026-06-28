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
          notebook hub ──spawns──▶ per-user env (role-chosen profile, tenant-bound, non-root,
                │ mints per-session capabilities       resource/network-limited)
                │                                       pod on k8s / container on compose (ADR-0018)
                ▼
   kernel holds opaque capability handles only ─▶ data API (X-IF-Capability, read-only)
                                                └▶ SQL proxy (SET ROLE tenant_reader_<uuid>)
```

The kernel's only credentials are opaque, single-tenant, read-only, revocable capability
handles minted by the hub at spawn (ADR-0012/0015) — never a session token, DB password, or
object-store secret.

## Deployment: one design, two spawners (ADR-0018)

The hub is JupyterHub; *how* it places each per-user environment is a deployment profile, not part
of the isolation design:

- **Kubernetes → KubeSpawner** — a per-user **pod**, contained by a single-user-pod egress
  **NetworkPolicy** (ADR-0009). This is the strongest containment and the production posture.
- **Compose / single host → DockerSpawner** — a per-user **container** on a dedicated internal
  network. So the platform's primary live deployment (ADR-0001) gets multi-tenant notebooks too,
  retiring the legacy root, auth-disabled, shared-credential Jupyter shim (ADR-0011 alt A).

Everything above the spawner is **spawner-independent and shared** by both: SSO (ADR-0014),
capability minting + the SQL proxy (ADR-0015), and the `tenant_reader_<uuid>` grants (ADR-0012).
The spawner only places the environment and injects the handles the upper layers mint.

**Non-parity, stated honestly (ADR-0018 dec 4):** allowlist-grade egress containment is a
Kubernetes property (the NetworkPolicy). On compose the kernel's boundary is the per-user,
**non-root**, **no-ambient-credential**, database-privilege-enforced isolation, with egress bounded
at the host/network level rather than per environment. Deployments needing strict egress
containment use the Kubernetes profile.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Per-tenant read-only DB role + tenant-scoped data path | **done** (DB-proven in CI) |
| 2 | Hub SSO + per-user spawner logic + runtime | **compose (DockerSpawner) live-validated; k8s (KubeSpawner) renders, not cluster-run** |
| 3 | Role-matched single-user images (authoring DS + analytics Voila) | **done** (both built & box-validated, non-root) |
| 4 | Capability minting + the SQL access proxy | **done** — wire backend + `verify-full` TLS + client SQL helper, **live-validated on the box** |
| 5 | Experiment-tracking gateway (ADR-0013/0019) | **done** — kernel→gateway tracking capability, tenant-namespaced experiments/models/artifacts, per-object pre-signed artifact URLs, **box-validated** |
| 6 | Tenant-scoped model browsing in the app | **done** — session-authed `/api/registered-models` read-path (prefix-stripped, source-run metrics) + redesigned Models page; experiment/run browsing still owed |

The design is decision-complete (ADR-0011→0015). Every piece except the Kubernetes hub runtime is
built **and validated on the live compose box**; only the KubeSpawner profile still needs a
running cluster.

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
  the profile from the role, bind the environment to its tenant with identity-only config
  (spawner-agnostic — consumed by KubeSpawner or DockerSpawner, ADR-0018).
- **Capability mint/resolve/revoke** (`services/notebook_hub/capabilities.py` + the
  verifier-side `capability_auth.py` in api_gateway and `binding.py` in the SQL proxy): opaque
  handles backed by a short-lived store entry, single-tenant, read-only, audience-bound
  (API vs SQL, not interchangeable), revoked by deleting the entry.
- **SQL proxy** (`services/notebook_sql_proxy`): the full Postgres-wire proxy — resolve an SQL
  handle → tenant, connect upstream over **`verify-full` TLS** (ADR-0017), `SET ROLE` into the
  reader role + `search_path` to its schema, relay; a denied handle opens no backend. The wire
  codec and the SCRAM-SHA-256 client (RFC 7677 vector) are unit-tested; **live-validated on the
  box** (read-only, single-tenant, bogus/API-audience handles refused).
- **Client** (`clients/python/industryflow`): `IndustryFlowClient` loads tenant data via the data
  API (`X-IF-Capability`); `IndustryFlowSQL` / `sql_query` run tenant SQL through the proxy with
  the SQL capability as the password (the `[sql]` extra). Holds no DB/object-store credential.

## Compose / DockerSpawner profile — BUILT & live-validated (ADR-0018)

The compose profile runs on the platform's primary live deployment and is **working end-to-end**:

- `notebook-hub` (JupyterHub + **DockerSpawner**, hub-managed chp) + `notebook-sso` (nginx
  `auth_request` → api-gateway `/auth/verify` → `X-IF-*` → hub), under the opt-in `notebooks`
  compose profile. The pluggable spawner is selected by `NOTEBOOK_SPAWNER` (`docker` here).
- Two **non-root single-user images** selected by role profile (ADR-0011 dec 5), built from
  `clients/python` and shipping the `industryflow` client: `Dockerfile.authoring` (JupyterLab + a
  lean DS stack + the `[sql]` extra for the proxy) and `Dockerfile.analytics` (Voila read-only
  dashboards, data-API path only). The hub picks the image per profile and lands analytics on
  `/voila`.
- The hub reaches the Docker socket via a **supplementary group** (`DOCKER_GID`), not as root.
- Run it: `docker compose --profile notebooks build` then
  `docker compose --profile notebooks up -d notebook-hub notebook-sso` → `https://<host>:8888`
  (see [getting-started](../getting-started.md)).

**Validated on the box:** platform SSO login → DockerSpawner spawned a **non-root (uid 1000)**,
tenant-bound notebook whose only data credentials are capability handles; the kernel read its
tenant's data via the data API. This **retires the legacy root, auth-disabled, shared-credential
`jupyter` shim** (ADR-0011 alt A, ADR-0018 dec 5).

## Embedding in the frontend

The notebook surface is meant to be **embedded in the platform UI** (operators land on an
`/analytics` view, data scientists on `/experiments`), not a separate site. The mechanism already
exists end-to-end:

- **Single sign-on** — the `notebook-sso` reverse proxy validates the platform session cookie via
  `GET /auth/verify` and forwards the verified identity to the hub (ADR-0014). A logged-in user
  reaches the hub with **no second login**, so the frontend can embed it directly (an `<iframe>`,
  or a routed view) pointing at the SSO endpoint.
- **What embedding needs** (the remaining work, all configuration, no new trust decisions):
  - **Single-origin routing** — serve the notebook SSO proxy under the *main* front door (e.g.
    `app.<host>/notebooks/…`) instead of the dedicated `:8888` port, so the session cookie is
    sent in the embedded context and origins match. This is the ADR-0014 refinement already
    flagged below.
  - **Frame-ancestors** — Jupyter/JupyterHub default to `X-Frame-Options: SAMEORIGIN`, which
    blocks cross-origin framing; with single-origin routing it is same-origin, or set a
    `Content-Security-Policy: frame-ancestors` for the app origin on the hub/single-user servers.
  - **Cookie attributes** — the `if_access` cookie must reach the embedded frame: same-site under
    single-origin routing (preferred), else `SameSite=None; Secure`.

So: **yes — binding notebooks into the frontend is supported by design**; what's left is the
single-origin routing + the two framing/cookie settings above, plus the `/analytics` and
`/experiments` pages themselves (deferred work below).

## Still to build

Shared by both profiles (built once, reused under either spawner):

- Curated **dashboard content** for the analytics (Voila) profile, and tightening it to
  render-only (the image ships Voila and the hub lands on `/voila`; locking the server API surface
  is a hardening follow-up).

**Kubernetes / KubeSpawner profile** (cluster-bound — needs a running cluster to validate):

- The **hub runtime** in the Helm chart behind `notebookHub.enabled` (hub + RBAC, the
  configurable-http-proxy, the SSO reverse proxy, the single-user egress NetworkPolicy, the
  Ingress) — these **render and lint** in CI (the hub image now sets `NOTEBOOK_SPAWNER=kube`) but
  have not run on a cluster.
- An end-to-end spawn on the cluster (issue #19).

**Single-origin routing** (notebooks under the main front door vs the dedicated `:8888` port,
ADR-0014) is a refinement on top of the working profile.

## Deferred to their own work

The experiment-tracking gateway (ADR-0013) and the frontend `/analytics` and `/experiments`
pages. Experiment tracking from notebooks remains gated until that gateway lands.
