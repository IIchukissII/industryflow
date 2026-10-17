<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Embedded notebooks

The notebook hub, its per-user environments, and the mediators a kernel reaches through capability
handles. Code in `services/notebook_hub/`, `services/notebook_sql_proxy/`,
`services/notebook_tracking_gateway/`, `services/notebook_cold_broker/` and `clients/python/` is
authoritative.

| Decision | ADR |
|---|---|
| Notebooks as untrusted, per-user, tenant-bound environments; role profiles | [ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) |
| No durable credential in the kernel; per-tenant read-only role | [ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md) |
| Tenant-namespaced tracking and registry | [ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) |
| SSO from the platform session; single origin | [ADR-0014](../../ADR/ADR-0014-notebook-hub-single-sign-on.md) |
| Capability minting, audiences, SQL proxy | [ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) |
| Database TLS (`verify-full`) | [ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md) |
| KubeSpawner / DockerSpawner, compose non-parity | [ADR-0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md) |
| Tracking gateway, default-deny routes, direct artifact bytes | [ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway.md) |
| Per-user persistence, idle reclamation | [ADR-0020](../../ADR/ADR-0020-notebook-per-user-persistence.md) |
| Cold-layer read path | [ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) dec 5 |

## Request path

```
browser ─▶ frontend edge /jupyter/ ─▶ notebook-sso ──auth_request──▶ api-gateway GET /auth/verify
                                          │ X-IF-* identity (client-supplied values overwritten)
                                          ▼
                                    notebook-hub ──spawns──▶ per-user environment (profile by role)
                                          │ mints capability handles
                                          ▼
kernel ─▶ api-gateway data API     (X-IF-Capability, audience api)
       ─▶ notebook-sql-proxy       (audience sql)      ─▶ TimescaleDB as tenant_sqlproxy_<uuid>, SET ROLE tenant_reader_<uuid>
       ─▶ notebook-tracking-gateway (audience tracking) ─▶ MLflow, tenant-namespaced; artifacts by pre-signed URL
       ─▶ notebook-cold-broker     (audience cold)     ─▶ pre-signed GETs under tenant_<uuid>/
```

The frontend `/notebooks` page iframes `/jupyter/` (`services/frontend/src/pages/Notebooks.jsx`). The
hub sends `Content-Security-Policy: frame-ancestors 'self'`.

## Components

| Component | Role | Port | Code / config |
|---|---|---|---|
| notebook-sso | nginx `auth_request` to `/auth/verify`, forwards `X-IF-*` | 8080 (internal) | `services/notebook_hub/sso-nginx.conf` |
| notebook-hub | JupyterHub, spawner by `NOTEBOOK_SPAWNER` (`docker` / `kube`), base URL `NOTEBOOK_BASE_URL` (`/jupyter/`) | 8000 | `services/notebook_hub/jupyterhub_config.py`, `identity.py`, `capabilities.py` |
| notebook-authoring image | JupyterLab, DS stack, `industryflow[sql]` client | | `services/notebook_hub/Dockerfile.authoring` |
| notebook-analytics image | Voila, data-API client; hub lands on `/voila` | | `services/notebook_hub/Dockerfile.analytics` |
| notebook-sql-proxy | Postgres-wire proxy | 6432 | `services/notebook_sql_proxy/` |
| notebook-tracking-gateway | MLflow proxy with per-route tenant rules; upload stage/commit | 5050 | `services/notebook_tracking_gateway/` |
| notebook-cold-broker | Pre-signed GETs for the tenant's cold prefix | 5060 | `services/notebook_cold_broker/` |
| Python client | `IndustryFlowClient`, `IndustryFlowSQL` / `sql_query`, `IndustryFlowCold`, `SKOPS_TRUSTED_TYPES` | | `clients/python/industryflow/` |

## Profiles

| Profile | Roles (default) | Handles | Persistence |
|---|---|---|---|
| `authoring` | `admin`, `engineer`, `data_scientist` | api, sql, tracking, cold | Per-user volume at `/home/jovyan/work` (`NOTEBOOK_VOLUME_TEMPLATE`, default `industryflow-nb-{username}`; PVC of `NOTEBOOK_STORAGE_CAPACITY`, default 2Gi, on Kubernetes) |
| `analytics` | every other role | api | none |

Unknown roles get `analytics`.

## Capability handles

| Audience | Verified by | Read-only |
|---|---|---|
| `api` | `services/api_gateway/capability_auth.py` | yes |
| `sql` | `services/notebook_sql_proxy/binding.py` | yes |
| `tracking` | `services/notebook_tracking_gateway/` | no |
| `cold` | `services/notebook_cold_broker/` | yes |

| Lifecycle | Detail |
|---|---|
| Mint | At spawn, one per audience, TTL `NOTEBOOK_CAPABILITY_TTL_SECONDS` (default 900) |
| Refresh | On each spawner poll while the server is healthy |
| Revoke | Server stop, respawn, or platform logout |
| SQL session re-check | Every `SQL_PROXY_REVALIDATE_SECONDS` (default 15); session ended once revoked |

## Kernel environment

| Variable | Profile | Meaning |
|---|---|---|
| `INDUSTRYFLOW_USER`, `INDUSTRYFLOW_COMPANY_ID`, `INDUSTRYFLOW_PROFILE` | both | Identity |
| `INDUSTRYFLOW_API_URL`, `INDUSTRYFLOW_API_CAPABILITY` | both | Data API |
| `INDUSTRYFLOW_SQL_PROXY_URL`, `INDUSTRYFLOW_SQL_CAPABILITY` | authoring | SQL proxy; the handle is the password |
| `INDUSTRYFLOW_TRACKING_URL`, `INDUSTRYFLOW_TRACKING_CAPABILITY` | authoring | Tracking gateway |
| `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_TOKEN` | authoring | MLflow client pointed at the gateway, handle as bearer token |
| `INDUSTRYFLOW_COLD_BROKER_URL`, `INDUSTRYFLOW_COLD_CAPABILITY` | authoring | Cold broker |

## Data API (api-gateway)

Each route accepts the platform session or an `X-IF-Capability` handle; a capability request runs
read-only.

| Method | Path |
|---|---|
| GET | `/api/measurements`, `/api/measurements/latest`, `/api/measurements/{sensor_id}` |
| GET | `/api/aggregations/{window}` (`start`, `end`, `order`, `limit` ≤ 1000), `/api/aggregations/{window}/latest`, `/api/aggregations/combined/{sensor_id}` |
| GET | `/api/training-data/equipment/{equipment_id}`, `/api/training-data/equipment/{equipment_id}/stream` |

## SQL proxy

| Item | Detail |
|---|---|
| Upstream login | `tenant_sqlproxy_<uuid>`, `NOINHERIT`, member of its own reader role only; password an HMAC of the tenant under a proxy-held key |
| Session | `SET ROLE tenant_reader_<uuid>`, `search_path` to the tenant schema; byte-transparent relay |
| Refused by Postgres | `SET ROLE` to another tenant, `RESET ROLE`, `set_config('role', …)`, `SET SESSION AUTHORIZATION` |
| Enrolment | `notebook_sql_proxy` role installs the login's SCRAM verifier on first use |
| Cancel | CancelRequest forwarded through a proxy-issued key |
| TLS | `verify-full` to the database |

## Tracking gateway

| Item | Detail |
|---|---|
| Names | Experiments and registered models prefixed `tenant_<uuid>.`, stripped on read |
| Artifacts | Under `tenant_<uuid>/`; the gateway sets each experiment's artifact location to that prefix |
| Routes | Only routes in `services/notebook_tracking_gateway/routes.py` are forwarded, each with its ownership rule; any other route → 403 |
| Request fields | Read from the query string (GET) or JSON body (other methods); the same field in the other place → 400 |
| Artifact bytes | Redirect to a pre-signed URL for the interior store; only upload staging is signed for the edge |

Experiments recorded at `mlflow-artifacts:/<id>` (created before the gateway set the location) are
rewritten by:

```bash
docker compose exec -T mlflow python - < scripts/migrate_tenant_artifact_locations.py           # dry run
docker compose exec -T mlflow python - --apply < scripts/migrate_tenant_artifact_locations.py   # write
```

It rewrites only experiments whose objects are all under the tenant prefix, moves no object, and is
safe to re-run.

## Deployment profiles

| | Compose (DockerSpawner) | Kubernetes (KubeSpawner) |
|---|---|---|
| Environment | Per-user container | Per-user pod |
| Egress | Network `industryflow-notebook-kernels`, shared with notebook-hub, api-gateway, notebook-sql-proxy, notebook-tracking-gateway, notebook-cold-broker, minio | Single-user egress NetworkPolicy (`networkpolicy.yaml`) |
| Docker access | Hub in supplementary group `DOCKER_GID` | RBAC for pods and PVCs (`notebook-hub.yaml`) |
| Enable | `docker compose --profile notebooks build` then `docker compose --profile notebooks up -d` | `notebookHub.enabled: true`, `notebookHub.baseUrl: /jupyter/` |
| URL | `https://<host>:<FRONTEND_HTTPS_PORT>/jupyter/` (default port 3443) | Frontend host, `/jupyter/` |
| Validation | Compose smoke checks the kernel network on every PR | Renders and lints in CI; not run on a cluster |

Idle culling: `jupyterhub-idle-culler`, timeout `NOTEBOOK_IDLE_TIMEOUT_SECONDS` (3600), interval
`NOTEBOOK_CULL_EVERY_SECONDS` (300), token scopes `list:users`, `read:users:activity`,
`read:servers`, `delete:servers`.

## Tests

| Test | Proves | Workflow |
|---|---|---|
| `infrastructure/timescaledb/tests/test_tenant_reader_isolation.py` | Reader role cannot read another tenant | `db-tenant-isolation.yml` |
| `infrastructure/timescaledb/tests/test_notebook_sql_login_isolation.py` | Proxy login cannot escape its reader role | `db-tenant-isolation.yml` |
| `services/notebook_sql_proxy/tests/test_sql_proxy_integration.py` | End-to-end through the running proxy | `db-tenant-isolation.yml` |
| `services/notebook_tracking_gateway/tests/test_*_integration.py` | Gateway routing and upload against live MLflow and object store | `tracking-gateway-integration.yml` |

## Not built

| Item |
|---|
| Curated Voila dashboard content; locking the analytics server API to render-only |
| `/analytics` and `/experiments` frontend pages |
| A Kubernetes cluster run of the hub |
