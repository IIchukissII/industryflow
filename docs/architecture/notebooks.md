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
                                                ├▶ SQL proxy (SET ROLE tenant_reader_<uuid>)
                                                ├▶ tracking gateway (tenant-namespaced MLflow)
                                                └▶ cold-store broker (tenant_<uuid>/ pre-signed GETs)
```

The kernel's only credentials are opaque, single-tenant, read-only, revocable capability
handles minted by the hub at spawn (ADR-0012/0015) — never a session token, DB password, or
object-store secret.

## Deployment: one design, two spawners (ADR-0018)

The hub is JupyterHub; *how* it places each per-user environment is a deployment profile, not part
of the isolation design:

- **Kubernetes → KubeSpawner** — a per-user **pod**, contained by a single-user-pod egress
  **NetworkPolicy** (ADR-0009). This is the strongest containment and the production posture.
- **Compose / single host → DockerSpawner** — a per-user **container** on a dedicated kernel
  network (`industryflow-notebook-kernels`). It holds only the hub, the trusted mediators (API
  gateway, SQL proxy, tracking gateway, cold broker) and the object store their pre-signed URLs name:
  the same set the Kubernetes NetworkPolicy allows. The capability store, the tracking server, Kafka,
  the database and Spark are not on it. Compose smoke asserts both halves on every PR. So the platform's primary live deployment (ADR-0001) gets multi-tenant notebooks too,
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
| 6 | Tenant-scoped model browsing in the app | **done** — session-authed `/api/registered-models` read-path (prefix-stripped, source-run metrics) + redesigned Models page, and the matching `/api/experiments` experiment/run read-path (ADR-0019 rev 1) |
| 7 | Per-user work persistence + idle reclamation (ADR-0020) | **done** — authoring `work/` on a per-user volume (Docker) / PVC (k8s); `jupyterhub-idle-culler` with a scoped token; compute stays ephemeral, capabilities re-minted per spawn |
| 8 | Cold-layer read path (ADR-0025 dec 5) | **built** — `cold`-audience capability → `notebook_cold_broker` (read-only cold principal) → tenant-prefix-scoped pre-signed GETs; `IndustryFlowCold` client (`list_files`/`read_all`). Compose-wired; not yet box-run |

**Artifact locations are recorded where the bytes are.** The gateway stores a kernel's artifacts
under the tenant prefix, and since #308 it also records that prefix as the experiment's artifact
location (it ignores any location the caller asks for). Serving loads models from the tracking
server directly, so the record must match the store. Experiments created before that were recorded
at `mlflow-artifacts:/<id>`, and serving could not load their models. Fix them once, per stack:

```bash
docker compose exec -T mlflow python - < scripts/migrate_tenant_artifact_locations.py           # dry run
docker compose exec -T mlflow python - --apply < scripts/migrate_tenant_artifact_locations.py   # write
```

It only rewrites an experiment whose objects are all under the tenant prefix, moves no object, and
is safe to re-run. On a stack with a public base URL, kernel artifact URLs are now signed for the
interior store; only browser upload staging is signed for the edge.

**The gateway forwards only routes it knows, each with an ownership rule.** Every MLflow route it
proxies is listed with how tenant ownership is checked: the experiment, run or logged model a request
names must belong to the caller, names are qualified into the caller's namespace, and a model
version's `source` must resolve into the caller's tenant. A request carries its fields in one place,
the one MLflow reads (the query string for GET, the JSON body otherwise); fields in the other place
are refused (400), so what the gateway checks is what MLflow acts on. Any route not on the list is
refused (403),
so a route a new MLflow release adds is closed until it gets a rule (ADR-0019 dec 5). Traces,
webhooks and MLflow's own pre-signed-URL routes are refused outright.

The design is decision-complete (ADR-0011→0015, ADR-0018→0020). Every piece except the Kubernetes hub runtime is
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
  handles backed by a short-lived store entry, single-tenant, audience-bound (not
  interchangeable), read-only except the tracking plane's (ADR-0015 dec 1 rev 1), revoked by
  deleting the entry. The hub records every handle it mints for a server, **refreshes their
  lifetime while the server is healthy** (each spawner poll), and **revokes them when the server
  stops, is replaced by a respawn, or its user logs out** of the platform.
- **SQL proxy** (`services/notebook_sql_proxy`): the full Postgres-wire proxy — resolve an SQL
  handle → tenant, connect upstream over **`verify-full` TLS** (ADR-0017) **as that tenant's own
  login** (`tenant_sqlproxy_<uuid>`), `SET ROLE` into the reader role + `search_path` to its
  schema, relay; a denied handle opens no backend. The relay is byte-transparent, so the session
  user is what bounds a kernel: the tenant login is `NOINHERIT` and a member of its own reader
  only, so `SET ROLE` into another tenant, `RESET ROLE`, `set_config('role', …)` and
  `SET SESSION AUTHORIZATION` are refused **by Postgres**. Its password is an HMAC of the tenant
  under a key only the proxy holds; the proxy installs the SCRAM verifier on first use through
  `notebook_sql_proxy`, an enrolment role that is a member of nothing and never carries a kernel's
  session. Each open session **re-checks its handle** (`SQL_PROXY_REVALIDATE_SECONDS`) and is
  ended once it is revoked, and a notebook interrupt (CancelRequest) is forwarded to the upstream
  query through a proxy-issued key. Proven against a real database by
  `test_notebook_sql_login_isolation.py` and end-to-end through the running proxy by
  `test_sql_proxy_integration.py`, both in the `db-tenant-isolation` workflow.
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

## Kernel environment, namespaces and persistence (concrete names)

The ADRs state these decisions abstractly; the names they are realised with live here.

- **What the spawner injects** (ADR-0012, ADR-0015, ADR-0019, ADR-0025): the identity
  (`INDUSTRYFLOW_USER`, `INDUSTRYFLOW_COMPANY_ID`, `INDUSTRYFLOW_PROFILE`), and per plane an
  endpoint plus its handle — `INDUSTRYFLOW_API_URL` / `INDUSTRYFLOW_API_CAPABILITY`,
  `INDUSTRYFLOW_SQL_PROXY_URL` / `INDUSTRYFLOW_SQL_CAPABILITY`, `INDUSTRYFLOW_TRACKING_URL` /
  `INDUSTRYFLOW_TRACKING_CAPABILITY`, `INDUSTRYFLOW_COLD_BROKER_URL` /
  `INDUSTRYFLOW_COLD_CAPABILITY`. For the authoring profile the MLflow client is pointed at the
  tracking gateway with `MLFLOW_TRACKING_URI`, and the tracking handle is sent as its bearer token
  through `MLFLOW_TRACKING_TOKEN` (ADR-0019 dec 1, 4).
- **Tenant namespace in the tracking store** (ADR-0019 dec 5): experiment and registered-model
  names carry a `tenant_<uuid>.` prefix — a dot, because MLflow forbids `/` and `:` in
  registered-model names — added on write and stripped on read; artifact keys sit under
  `tenant_<uuid>/` in the object store.
- **Per-user work persistence** (ADR-0020): the authoring profile's per-user volume (a named
  Docker volume, or a PVC on Kubernetes) is mounted at `/home/jovyan/work`; the rest of the home
  comes from the image. The idle culler (`jupyterhub-idle-culler`) holds a token scoped to
  `list:users`, `read:users:activity`, `read:servers` and `delete:servers`.

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
