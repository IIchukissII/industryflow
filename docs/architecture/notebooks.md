<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Embedded notebooks

The notebook hub, its per-user environments, and the planes a kernel reaches through capability
handles.

| Decision | ADR |
|---|---|
| Notebooks as untrusted, per-user, tenant-bound environments; role profiles | [ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) |
| No durable credential in the kernel; per-tenant read-only role | [ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md) |
| Tenant-namespaced tracking and registry | [ADR-0013](../../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) |
| Notebook SSO from the platform session; single origin | [ADR-0014](../../ADR/ADR-0014-notebook-hub-single-sign-on.md) |
| Capability minting, audiences, SQL proxy | [ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) |
| Database TLS (`verify-full`) | [ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md) |
| KubeSpawner / DockerSpawner, Compose non-parity | [ADR-0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md) |
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
kernel ─▶ api-gateway data API      (X-IF-Capability, audience api)
       ─▶ notebook-sql-proxy        (audience sql)      ─▶ TimescaleDB as tenant_sqlproxy_<uuid>, SET ROLE tenant_reader_<uuid>
       ─▶ notebook-tracking-gateway (audience tracking) ─▶ MLflow, tenant-namespaced; artifacts by pre-signed URL
       ─▶ notebook-cold-broker      (audience cold)     ─▶ pre-signed GETs under tenant_<uuid>/
```

The frontend `/notebooks` page iframes `/jupyter/` (`services/frontend/src/pages/Notebooks.jsx`); the
hub sends `Content-Security-Policy: frame-ancestors 'self'`.

## Components

| Name | Role | Port | Config |
|---|---|---|---|
| `notebook-sso` | nginx `auth_request` to `/auth/verify`; forwards `X-IF-*`; adds `X-IF-SSO-Proxy-Secret` on hub routes only | 8080 | `services/notebook_hub/sso-nginx.conf` |
| `notebook-hub` | JupyterHub; spawns and culls per-user environments, mints capability handles; login only with the SSO proxy secret | 8000 (chp, platform network only), 8081 (hub API) | [notebook hub](../../services/notebook_hub/README.md) |
| `notebook-authoring` image | JupyterLab, data-science stack, `industryflow[sql,cold]` client | | `services/notebook_hub/Dockerfile.authoring` |
| `notebook-analytics` image | Voila, data-API client | | `services/notebook_hub/Dockerfile.analytics` |
| `notebook-sql-proxy` | SQL proxy (Postgres wire) | 6432 | [SQL proxy](../../services/notebook_sql_proxy/README.md) |
| `notebook-tracking-gateway` | Tracking gateway (MLflow, per-route tenant rules); upload stage and commit | 5050 | [tracking gateway](../../services/notebook_tracking_gateway/README.md) |
| `notebook-cold-broker` | Cold-store broker: pre-signed GETs for the tenant's cold prefix | 5060 | `services/notebook_cold_broker/` |
| Python client | `IndustryFlowClient`, `IndustryFlowSQL`, `IndustryFlowCold`, `SKOPS_TRUSTED_TYPES` | | [Python client](../../clients/python/README.md) |

## Profiles

| Profile | Roles (default) | Image | Landing | Capability handles | Persistence |
|---|---|---|---|---|---|
| `authoring` | `admin`, `engineer`, `data_scientist` | `NOTEBOOK_IMAGE_AUTHORING` | JupyterLab | api, sql; tracking when `INDUSTRYFLOW_TRACKING_URL` is set; cold when `INDUSTRYFLOW_COLD_BROKER_URL` is set | Per-user volume `NOTEBOOK_VOLUME_TEMPLATE` at `<home>/work`; a PVC on Kubernetes |
| `analytics` | Every other role | `NOTEBOOK_IMAGE_ANALYTICS` | `/voila` | api | None |

Either image falls back to `NOTEBOOK_IMAGE`. Hub settings:
[notebook hub](../../services/notebook_hub/README.md#settings).

## Capability handles

| Audience | Redeemed by | Read-only |
|---|---|---|
| `api` | `services/api_gateway/capability_auth.py` | yes |
| `sql` | `services/notebook_sql_proxy/binding.py` | yes |
| `tracking` | `services/notebook_tracking_gateway/` | no |
| `cold` | `services/notebook_cold_broker/` | yes |

| Event | Effect |
|---|---|
| Spawn | Handles of an earlier run of the server are revoked; one handle per audience is minted with lifetime `NOTEBOOK_CAPABILITY_TTL_SECONDS` and recorded in `nbcap-user:<user>` |
| Spawner poll, server healthy | Lifetime extended |
| Server stops (user, culler, hub logout) | `post_stop_hook` revokes its handles |
| Platform logout | The frontend logs out of the hub first (`shutdown_on_logout` stops the server); `/auth/logout` revokes all the user's handles; a server whose handles are all revoked stops at its next poll |
| Login (`/hub/login`) | Refused (401) unless `X-IF-SSO-Proxy-Secret` matches `NOTEBOOK_SSO_PROXY_SECRET` |
| Hub request after `NOTEBOOK_AUTH_REFRESH_SECONDS`, and before a spawn | Hub login compared with the forwarded identity (identity headers without the proxy secret count as none): a different user ends the login; the same user with another tenant or role ends it and stops their servers |
| Open SQL session | Re-checked every `SQL_PROXY_REVALIDATE_SECONDS`; closed once the handle is revoked |

## Kernel environment

| Name | Profile | Meaning |
|---|---|---|
| `INDUSTRYFLOW_USER`, `INDUSTRYFLOW_COMPANY_ID`, `INDUSTRYFLOW_PROFILE` | both | Identity |
| `INDUSTRYFLOW_API_URL`, `INDUSTRYFLOW_API_CAPABILITY` | both | Data API |
| `INDUSTRYFLOW_SQL_PROXY_URL`, `INDUSTRYFLOW_SQL_CAPABILITY` | authoring | SQL proxy; the handle is the password |
| `INDUSTRYFLOW_TRACKING_URL`, `INDUSTRYFLOW_TRACKING_CAPABILITY` | authoring | Tracking gateway |
| `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_TOKEN` | authoring | MLflow client pointed at the tracking gateway, handle as bearer token |
| `INDUSTRYFLOW_COLD_BROKER_URL`, `INDUSTRYFLOW_COLD_CAPABILITY` | authoring | Cold-store broker |

A URL variable is set only when the hub has it configured; the tracking and cold handles are minted
only then. Outside a hub-spawned kernel, `IndustryFlowSQL.from_env()` and `IndustryFlowCold.from_env()`
raise.

## Planes

| Plane | Mediator | Detail |
|---|---|---|
| Data API | `api-gateway` | Read endpoints accept an `api` handle and run read-only ([endpoints](../api/README.md#api-gateway-endpoints)) |
| SQL | SQL proxy | [principals and mechanism](../../services/notebook_sql_proxy/README.md#database-principals) |
| Tracking | Tracking gateway | [endpoints and rules](../../services/notebook_tracking_gateway/README.md#endpoints) |
| Cold history | Cold-store broker | [cold layer](data-and-storage.md#cold-layer) |
| Model upload | `ml-service-api`, tracking gateway | [model upload](model-upload.md) |

Experiments recorded at `mlflow-artifacts:/<id>` (created before the tracking gateway set the
location) are rewritten by:

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
| Egress | Network `industryflow-notebook-kernels`, shared with `notebook-hub`, `api-gateway`, `notebook-sql-proxy`, `notebook-tracking-gateway`, `notebook-cold-broker`, `minio` | Single-user egress NetworkPolicy (`networkpolicy.yaml`) |
| Docker access | Hub in supplementary group `DOCKER_GID` | RBAC for pods and PVCs (`notebook-hub.yaml`) |
| Enable | [getting started, step 6](../getting-started.md#6-notebooks-optional-profile) | `notebookHub.enabled: true`, `notebookHub.baseUrl: /jupyter/` |
| Validation | Compose smoke checks the kernel network on every PR | Renders and lints in CI; not run on a cluster |

Idle culling: `jupyterhub-idle-culler` with token scopes `list:users`, `read:users:activity`,
`read:servers`, `delete:servers`.

## Isolation tests

| Test | Proves | Workflow |
|---|---|---|
| `infrastructure/timescaledb/tests/test_tenant_reader_isolation.py` | Reader role cannot read another tenant | `db-tenant-isolation.yml` |
| `infrastructure/timescaledb/tests/test_notebook_sql_login_isolation.py` | Proxy login cannot escape its reader role | `db-tenant-isolation.yml` |

Proxy and gateway tests: their READMEs.

## Not built

| Item |
|---|
| Curated Voila dashboard content; locking the analytics server API to render-only |
| `/analytics` and `/experiments` frontend pages |
| A Kubernetes cluster run of the notebook hub |
