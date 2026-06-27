<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Embedded notebooks (analytics & experimentation)

The *what* and *how*; the **why** is in [ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md)
(shape), [ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md) (credentials), and
[ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) (experiment tracking).

The platform is growing in-product analytics (for operators) and experimentation (for data
scientists) as per-user, isolated notebook environments embedded in the frontend. Because a
kernel runs **user-authored code**, it is treated as untrusted: it holds no shared or ambient
credential, and tenant isolation for it is enforced by database privilege, not by the
trusted-code `search_path` discipline used elsewhere (ADR-0003).

## Status

Delivery is phased. **Phase 1 (the keystone) is in place**; the interactive surfaces are not
yet built.

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Per-tenant read-only DB role + the tenant-scoped data path | **done** |
| 2 | JupyterHub + per-user spawner + SSO from the session cookie | **in progress** |
| 3 | Operator read-only surface (rendered dashboards) | planned |
| 4 | Author surface (JupyterLab) + the SQL proxy + per-session capability minting | planned |
| 5 | Experiment-tracking gateway (ADR-0013) | planned |

**Phase 2 so far:** the SSO decision is recorded (ADR-0014) — the hub authenticates from the
platform session via a trusted proxy and never re-verifies the token or runs its own login. The
spawner's decision logic (`services/notebook_hub/identity.py`) is implemented and unit-tested:
it reads the verified identity, chooses the spawn profile from the role (read-only analytics vs
authoring), and binds the pod to its tenant with identity-only environment — no data credential
(ADR-0012 dec 5). A reference `jupyterhub_config.py` wires this into JupyterHub/KubeSpawner.

The hub **runtime manifests** are drafted in the Helm chart behind `notebookHub.enabled`
(default off): the hub Deployment + RBAC, the configurable-http-proxy, the **SSO reverse proxy**
that performs the ADR-0014 handoff (validate the session against api-gateway, forward the
verified `X-IF-*` identity, overwrite any client-supplied one), the notebooks Ingress, and the
single-user pod egress NetworkPolicy (data API + DNS only). These **render and lint** but are
**not yet cluster-validated**.

The **api-gateway session-verify endpoint** the SSO proxy calls is implemented at
`GET /auth/verify` (ADR-0014 handoff contract): it validates the platform session with the one
existing verification and returns the verified identity in `X-IF-*` headers; a request without a
valid session or tenant is rejected, which the proxy treats as "deny".

Still pending: running the hub on a cluster, the per-session capability minting + SQL proxy
(ADR-0012), and the notebook images.

## What exists today (phase 1)

**Per-tenant read-only role.** `create_tenant_schema()` provisions a `tenant_reader_<uuid>`
role per tenant: `NOLOGIN`, read-only, granted only its own schema (USAGE + SELECT on current
and future tables; TimescaleDB propagates SELECT to hypertable chunks). A cross-tenant query
made as this role **fails at the GRANT level** — it does not depend on the querying code
behaving. It is `NOLOGIN` because the future SQL proxy assumes it via `SET ROLE`, so no
password exists to place in a kernel (ADR-0012 dec 4). Existing tenants are backfilled by
`infrastructure/timescaledb/init-scripts/09-tenant-reader-roles-migration.sql`. The boundary
is proven by `infrastructure/timescaledb/tests/test_tenant_reader_isolation.py` in the
`db-tenant-isolation` CI workflow.

**Tenant-scoped data path.** The default way a notebook will read data is the existing
`api_gateway` read API, called **as the user** so the standard per-tenant scoping
(`get_db_with_tenant`, ADR-0003) applies:

- `GET /api/measurements` (and `/{sensor_id}`) — raw readings; supports `start`/`end`/`order`
  for time-window/analytics pulls, capped at 1000 rows/request.
- `GET /api/aggregations/{window}` — `1min`/`5min`/`1hour` rollups; same time-range params.
- `GET /api/training-data/equipment/{id}` (+ `/stream`) — bulk per-equipment datasets for
  DataFrame loading (JSON to 100k rows, or streamed CSV).

**Client skeleton.** `clients/python/industryflow` wraps that API into pandas DataFrames — the
blessed path notebook images will ship. It carries a per-session capability as a bearer token
and never holds a database or object-store credential.

## The two data paths (target)

- **API path (default, all profiles).** The kernel calls the read API as the user with a
  per-session capability (ADR-0012). No database credential in the kernel.
- **Direct SQL (authoring profile).** The kernel connects to a trusted SQL proxy presenting
  its capability; the proxy holds the privileged principal and `SET ROLE`s into the tenant's
  `tenant_reader_<uuid>` for that session. The per-tenant role above is what makes this safe.

## Deferred

The spawner, SSO authenticator, the SQL proxy and per-session capability minting, the
experiment-tracking gateway, and the frontend pages are later phases. Experiment tracking from
notebooks remains gated until its multi-tenancy gateway lands (ADR-0013).
