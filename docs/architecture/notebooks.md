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

| Frontend page | Shows | Source |
|---|---|---|
| `/notebooks` | `/jupyter/` in an iframe: the role's profile ([profiles](#profiles)), so the `analytics` landing for every role not mapped to authoring | `services/frontend/src/pages/Notebooks.jsx` |
| `/experiments` | The tenant's experiments and their runs (params, metrics, status, the registered model a run became), read-only, from `/api/experiments` | `services/frontend/src/pages/Experiments.jsx` |

The hub sends `Content-Security-Policy: frame-ancestors 'self'`.

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
| Python client | `IndustryFlowClient`, `IndustryFlowSQL`, `IndustryFlowCold`, `SKOPS_TRUSTED_TYPES`, `log_feature_sample` | | [Python client](../../clients/python/README.md) |

## Profiles

| Profile | Roles (`NOTEBOOK_AUTHORING_ROLES`, default) | Image | Landing | Capability handles | Persistence |
|---|---|---|---|---|---|
| `authoring` | `admin`, `engineer` | `NOTEBOOK_IMAGE_AUTHORING` | JupyterLab | api, sql; tracking when `INDUSTRYFLOW_TRACKING_URL` is set; cold when `INDUSTRYFLOW_COLD_BROKER_URL` is set | Per-user volume `NOTEBOOK_VOLUME_TEMPLATE` at `<home>/work`; a PVC on Kubernetes |
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

## Capability-use record

[ADR-0042](../../ADR/ADR-0042-capability-use-audit.md). Planes append events to the stream
`nbcap-audit` in the capability store, atomically with the store change they describe; the API
gateway's writer (`services/api_gateway/messaging/capability_audit_drain.py`, consumer group
`capability-audit-writer`) drains it into `public.capability_use_events` through
`record_capability_use()`. Contract: `libs/industryflow_contracts/industryflow_contracts/capability_audit.py`.

| Event | Recorded by | When |
|---|---|---|
| `minted` | hub, ml-service (upload) | With the mint, in one transaction |
| `first_use` | each plane that redeems a handle | Its first use of the handle (marker `nbcap-audit-seen:<plane>:<handle_id>`, 7 days) |
| `refused` | each plane that redeems a handle | A refusal naming a real entry: once per `handle_id`, plane and reason per 5 minutes (`nbcap-audit-repeat:`), the rest counted (`nbcap-audit-repeats:`, 7 days) into the next record's `suppressed`. A value naming none: once per plane and digest per hour (`nbcap-audit-refused:`), at most 60 per plane and source address per minute (`nbcap-audit-source:`), `detail` = `source=<address>` |
| `revoked` | hub, API gateway (logout), tenant delete | With the removal, only if it removed a live entry |
| `consumed` | tracking gateway (upload) | With the claim of a single-use upload handle |
| `environment_started`, `environment_stopped` | hub | Server spawn and stop |
| `sql_session_opened`, `sql_session_closed` | SQL proxy | Session start and end, once per `handle_id` and kind per 5 minutes, the rest counted like a refusal's; none for a handle with no `handle_id`; statements are not recorded |

| Column | Meaning |
|---|---|
| `handle_id` | Non-secret identifier bound into the handle's entry at mint; a handle minted before the record has none |
| `digest` | First 16 hex of SHA-256 of a presented value that named no entry; absent otherwise |
| `reason`, `detail` | Why it was refused, revoked or closed; the server or session |
| `suppressed` | How many of the same event the entry repeated, unrecorded, since its last record |
| Source address | The client address the edge reported (`X-Real-IP`) when the peer is one of `EDGE_PROXY_PEERS`; the connecting peer otherwise |

| Read | Who |
|---|---|
| `GET /api/capability-use?company_id=&handle_id=&since=&before_id=&limit=` | Superuser: any tenant or all; tenant `admin`: their own tenant only |

| Setting (API gateway) | Default | Meaning |
|---|---|---|
| `CAPABILITY_AUDIT_RETENTION_DAYS` | `365` | Rows older are expired hourly by `expire_capability_use()`; the database refuses fewer than 30 |
| `EDGE_PROXY_PEERS` (also tracking gateway) | compose `frontend`; Helm empty | The edge's addresses, networks or host names. Only addresses no kernel can hold: on Kubernetes not the pod network (kernel pods share it) and not a Service name (its ClusterIP is never the peer); empty is safe |

| Metric / alert | Meaning |
|---|---|
| `capability_audit_backlog` | Events in the stream not yet written (written ones are deleted from it) |
| `capability_audit_events_total{result}` | `written`, `unreadable` (skipped), `refused` (by the database, skipped), `retry` |
| `IndustryFlowCapabilityAuditBacklog` | Backlog above 1000 for 15m ([monitoring](../operations/monitoring.md)) |
| `IndustryFlowCapabilityEventsDropped` | An `unreadable` or `refused` event in 15m ([monitoring](../operations/monitoring.md)) |

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

A model a kernel registers is not served until a user with a writing role promotes a version from the
Models page ([promotion](ml-and-features.md#promotion)). Its drift baseline is the feature sample logged
in the same run:

```python
with mlflow.start_run():
    mlflow.sklearn.log_model(model, name="model", registered_model_name="vpd-regressor")
    industryflow.log_feature_sample(train[["temp", "humidity"]])  # columns = the feature config's base sensors
```

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
| Enable | [getting started, step 6](../getting-started.md#6-notebooks-optional-profile) | `notebookHub.enabled: true`, `notebookHub.baseUrl: /jupyter/`, `notebookHub.authoringRoles` |
| Validation | Compose smoke checks the kernel network on every PR | Renders and lints in CI; not run on a cluster |

| Hub service | Does | Token scopes |
|---|---|---|
| `idle-culler` | Stops idle servers | `list:users`, `read:users:activity`, `read:servers`, `delete:servers` |
| `volume-reclaim` | For each deletion awaiting it, deletes the tenant's users from the hub (each spawner's `delete_forever` removes the user's volume or PVC), checks the Docker volume or PVC by the spawner's own name for it and removes any left behind, then records `notebook_volumes` only when none remains; database role `notebook_hub_reclaim_user` ([ADR-0020](../../ADR/ADR-0020-notebook-per-user-persistence.md) dec 6). Runs when `NOTEBOOK_RECLAIM_DB_PASSWORD` is set | `delete:users` |

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
| A Kubernetes cluster run of the notebook hub |

## ADR-0011 dec 1

| Element | Form |
|---|---|
| Spawn | On demand, one server per user, from the hub's verified identity |
| Limits | CPU and memory per profile: analytics 0.5 CPU, 1G; authoring 2 CPU, 4G |
| Ephemerality | DockerSpawner removes the container when its server stops; only `<home>/work` of an authoring user persists ([ADR-0020](../../ADR/ADR-0020-notebook-per-user-persistence.md)) |
| Idle reclamation | Hub service `idle-culler` stops servers idle past `NOTEBOOK_IDLE_TIMEOUT_SECONDS` (3600), checked every `NOTEBOOK_CULL_EVERY_SECONDS` (300) |

| Code | Role |
|---|---|
| `services/notebook_hub/jupyterhub_config.py::bind_identity_and_profile` | Per-user spawn and its limits |
| `services/notebook_hub/jupyterhub_config.py::PROFILE_RESOURCES` | CPU and memory per profile |
| `services/notebook_hub/jupyterhub_config.py::IDLE_TIMEOUT_SECONDS` | Culler timeout; the `idle-culler` service and its scoped token |
| `services/notebook_hub/requirements.txt` | `jupyterhub-idle-culler` pin |
| `services/notebook_hub/tests/test_jupyterhub_config.py::test_idle_culler_service_survives` | Proof: the culler service is configured |

## ADR-0011 dec 3

| Principal | Form |
|---|---|
| `tenant_reader_<uuid>` | NOLOGIN; `USAGE` on its one tenant schema, `SELECT` on its tables, default `SELECT` on tables created later; nothing else |

`create_tenant_schema()` creates the reader with the schema; `ensure_tenant_reader_role()` backfills
it for existing schemas. A query on another tenant's schema fails at the grant.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/04-complete-tenant-creation.sql::create_tenant_schema` | Reader created with the tenant |
| `infrastructure/timescaledb/init-scripts/09-tenant-reader-roles-migration.sql::ensure_tenant_reader_role` | Reader backfill, idempotent |
| `infrastructure/timescaledb/tests/test_tenant_reader_isolation.py::test_reader_cannot_read_another_tenant` | Proof: cross-tenant read refused |
| `infrastructure/timescaledb/tests/test_tenant_reader_isolation.py::test_reader_cannot_write_its_own_tenant` | Proof: read-only |
| `.github/workflows/db-tenant-isolation.yml` | Runs the proofs against a real database |

## ADR-0011 dec 4

| Element | Form |
|---|---|
| Client | `IndustryFlowClient` sends the `api` handle in `X-IF-Capability` to `INDUSTRYFLOW_API_URL` |
| Gateway | A request without a session falls back to the handle; a handle-sourced request runs in a read-only transaction under its tenant's search path |
| Analytics profile | Only the `api` handle is minted; the image installs the client without the `sql` extra |
| Kubernetes egress | The single-user policy allows the API gateway port |

| Code | Role |
|---|---|
| `clients/python/industryflow/client.py::IndustryFlowClient` | Data-API client |
| `services/api_gateway/dependencies.py::optional_active_user` | Session optional, handle fallback |
| `services/api_gateway/dependencies.py::get_tenant_db` | Read-only session for a handle |
| `services/notebook_hub/capabilities.py::AUDIENCE_API` | The data-API audience |
| `services/notebook_hub/Dockerfile.analytics` | Analytics image, client without SQL |
| `deploy/helm/industryflow/templates/networkpolicy.yaml` | Egress to the API gateway |

## ADR-0011 dec 5

`select_profile` maps the verified role to `authoring` when it is a configured authoring role and to
`analytics` otherwise; the spawn hook sets the profile's image, landing (`/voila` for analytics),
limits, handles and persistence ([profiles](#profiles)). The user is offered no choice.

| Code | Role |
|---|---|
| `services/notebook_hub/identity.py::select_profile` | Role to profile |
| `services/notebook_hub/identity.py::image_for_profile` | Image per profile |
| `services/notebook_hub/jupyterhub_config.py::bind_identity_and_profile` | Profile applied at spawn |
| `services/notebook_hub/Dockerfile.authoring` | Authoring image (JupyterLab) |
| `services/notebook_hub/Dockerfile.analytics` | Analytics image (Voila) |
| `scripts/check_model_env_parity.py::KERNEL_DOCKERFILE` | The authoring kernel's pins, held to serving's |
| `services/notebook_hub/tests/test_identity.py::test_other_roles_get_readonly_analytics` | Proof: an unmapped role gets analytics |

## ADR-0011 dec 7

| Containment | Compose | Kubernetes |
|---|---|---|
| Egress | Network `industryflow-notebook-kernels` ([ADR-0018 dec 4](#adr-0018-dec-3-4)) | NetworkPolicy `notebook-singleuser-egress`: DNS, hub API, API gateway, SQL proxy, tracking gateway, cold broker (with cold export), MinIO (in cluster, or `networkPolicy.externalEgress.minio`) |
| Process | uid `1000:1000`, all capabilities dropped, `no-new-privileges` | uid 1000, `runAsNonRoot`, no privilege escalation, all capabilities dropped |
| Resources | CPU and memory per profile | CPU and memory per profile |
| Ephemerality | Container removed at stop | Pod removed at stop |
| Per-tenant concurrency and quota caps | Not built | Not built |

| Code | Role |
|---|---|
| `services/notebook_hub/jupyterhub_config.py::PROFILE_RESOURCES` | Resource limits |
| `services/notebook_hub/jupyterhub_config.py` | Spawner security settings |
| `deploy/helm/industryflow/templates/networkpolicy.yaml` | Single-user egress allowlist |
| `docker-compose.yml` | Kernel network membership |

## ADR-0012 dec 2

| Plane | What the kernel holds | Credential that reaches the store |
|---|---|---|
| SQL | Its `sql` handle, sent as the Postgres password | The tenant login's password, derived inside the proxy from `SQL_PROXY_TENANT_LOGIN_KEY` |
| Cold history | Its `cold` handle | None: the broker returns pre-signed GETs |

| Code | Role |
|---|---|
| `services/notebook_sql_proxy/proxy.py::serve_connection` | Handle in, tenant session out |
| `services/notebook_sql_proxy/binding.py::tenant_login_password` | Password derivation, proxy only |
| `clients/python/industryflow/cold.py::IndustryFlowCold` | Cold client, handle only |
| `services/notebook_cold_broker/broker.py` | Pre-signed GETs for the handle's tenant |
| `services/notebook_hub/tests/test_identity.py::test_pod_environment_carries_identity_not_credentials` | Proof: no credential in the identity environment |

## ADR-0012 dec 4

| Principal | Form |
|---|---|
| `tenant_sqlproxy_<uuid>` | LOGIN, NOINHERIT; member of its own `tenant_reader_<uuid>` and the privilege-free `notebook_sql_tenants`; session user of every kernel session |
| `tenant_reader_<uuid>` | NOLOGIN, `SELECT` on one schema; the proxy `SET ROLE`s into it |
| `notebook_sql_proxy` | Enrolment principal; member of nothing; `EXECUTE` on `notebook_sql_proxy_enrol()` only |

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/13-notebook-sql-proxy-role.sh::ensure_tenant_sql_login` | Per-tenant login |
| `infrastructure/timescaledb/init-scripts/13-notebook-sql-proxy-role.sh::notebook_sql_proxy_enrol` | Login enrolment |
| `infrastructure/timescaledb/init-scripts/09-tenant-reader-roles-migration.sql::ensure_tenant_reader_role` | Per-tenant reader |
| `services/notebook_sql_proxy/binding.py::session_setup_statements` | `SET ROLE` into the reader |
| `infrastructure/timescaledb/tests/test_notebook_sql_login_isolation.py::test_set_role_into_another_tenants_reader_is_refused` | Proof: a login reaches no other tenant |
| `infrastructure/timescaledb/tests/test_tenant_reader_isolation.py` | Proof: the reader reads one tenant |
| `.github/workflows/db-tenant-isolation.yml` | Runs both proofs |

## ADR-0012 dec 5

An entry binds `user`, `company_id`, `audience`, `read_only` and `handle_id`. `read_only` is false for
`tracking` only; the tracking gateway bounds its writes to the tenant namespace. A verifier resolves
an entry for its own audience only and takes the tenant from the entry. The kernel's environment
carries identity and handles, nothing else.

| Code | Role |
|---|---|
| `services/notebook_hub/capabilities.py::Binding` | Bound facts |
| `services/notebook_hub/capabilities.py::mint` | Entry written, `read_only` per audience |
| `services/notebook_hub/capabilities.py::resolve` | Audience-checked resolution |
| `services/notebook_hub/identity.py::pod_environment` | Identity-only environment |
| `services/notebook_hub/tests/test_identity.py::test_pod_environment_carries_identity_not_credentials` | Proof: identity only |

## ADR-0012 dec 6

| Event | Revocation |
|---|---|
| Server stops | `post_stop_hook` deletes the server's entries |
| Platform logout | `/auth/logout` deletes every entry in the user's index and upload index; the hub stops the server at its next poll |
| Password change or reset, deactivation, deletion, company deactivation | `end_all_sessions` revokes refresh tokens and capabilities |
| UI sign-out | `signOut` logs out of the hub first, then calls `/auth/logout` |
| Open SQL session | Re-checked every `SQL_PROXY_REVALIDATE_SECONDS` (15); closed once the handle no longer resolves |

| Code | Role |
|---|---|
| `services/notebook_hub/jupyterhub_config.py::revoke_server_capabilities` | Revocation at server stop |
| `services/api_gateway/capability_auth.py::revoke_user_capabilities` | Revocation of all a user's handles |
| `services/api_gateway/routers/auth_tokens.py::logout` | Logout revokes |
| `services/api_gateway/messaging/refresh_tokens.py::end_all_sessions` | Account events revoke |
| `services/frontend/src/services/session.js::signOut` | Chained sign-out |
| `services/notebook_sql_proxy/server.py` | Session re-check interval |
| `services/api_gateway/tests/test_capability_auth.py` | Proof: logout revokes |
| `services/notebook_sql_proxy/tests/test_sql_proxy_integration.py` | Proof: an open session ends on revocation |

## ADR-0013 dec 1

MLflow, its database and the tracking-store credentials sit on the platform network only. The
kernel network holds none of them, and the Kubernetes single-user egress policy allows no route to
them. The kernel reaches the artifact store only by a gateway-signed URL.

| Code | Role |
|---|---|
| `docker-compose.yml` | Kernel network without MLflow |
| `deploy/helm/industryflow/templates/networkpolicy.yaml` | No egress to MLflow |
| `.github/workflows/compose-smoke.yml` | Proof: a kernel cannot reach MLflow |

## ADR-0013 dec 2, 3

The tracking gateway scopes every tracking, registry and artifact operation to the caller's tenant.

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway` | The tenant-scoping gateway |

## ADR-0013 dec 4

Experiments are read through the product, never the tracking server's own UI.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/experiments.py` | Tenant-scoped experiment read |
| `services/frontend/src/pages/Experiments.jsx` | Experiments page |

## ADR-0014 dec 1-3

The edge asks the platform's session verification on every hub request and forwards the verified identity with a proxy credential; the hub accepts only that.

| Code | Role |
|---|---|
| `services/notebook_hub/sso-nginx.conf` | Session check and identity forwarding |
| `services/notebook_hub/jupyterhub_config.py` | Forwarded-identity authenticator |

## ADR-0014 dec 4

`NOTEBOOK_AUTHORING_ROLES` (default `admin,engineer`) names platform roles; an empty list or a role
outside the platform's role set stops the hub at config load. The spawn hook selects the profile
from the role in the hub's auth state and mints every handle against the tenant there.

| Code | Role |
|---|---|
| `services/notebook_hub/identity.py::parse_authoring_roles` | Mapping parsed, unknown role refused |
| `services/notebook_hub/identity.py::PLATFORM_ROLES` | The platform's roles |
| `services/notebook_hub/jupyterhub_config.py::bind_identity_and_profile` | Profile and tenant at spawn |
| `deploy/helm/industryflow/values.yaml` | `notebookHub.authoringRoles` |
| `deploy/helm/industryflow/templates/notebook-hub.yaml` | Mapping passed to the hub |
| `docker-compose.yml` | Mapping passed to the hub |
| `services/notebook_hub/tests/test_jupyterhub_config.py::test_the_hub_does_not_start_with_an_unknown_authoring_role` | Proof: an unknown role stops the hub |
| `tests/config_guards/test_notebook_roles.py` | Proof: the hub's role set equals the gateway's |

## ADR-0014 dec 7

Platform sign-out signs out of the hub first.

| Code | Role |
|---|---|
| `services/frontend/src/services/session.js` | Chained sign-out |

## ADR-0015 dec 1-4

The hub mints audience-bound handles into the capability store at spawn; revocation deletes the entry.
Each verifier resolves an entry only when its `audience` is the verifier's own. The tracking gateway
resolves `tracking` and `upload` separately and applies upload admission to `upload` only.

| Code | Role |
|---|---|
| `services/notebook_hub/capabilities.py` | Minting and revocation |
| `services/notebook_hub/capabilities.py::resolve` | Audience check at resolution |
| `services/api_gateway/capability_auth.py::AUDIENCE_API` | Data-API audience |
| `services/notebook_sql_proxy/binding.py::AUDIENCE_SQL` | SQL audience |
| `services/notebook_tracking_gateway/policy.py::AUDIENCE_UPLOAD` | Tracking and upload audiences |
| `services/notebook_cold_broker/policy.py::AUDIENCE_COLD` | Cold audience |
| `services/notebook_hub/tests/test_capabilities.py` | Proof: a handle resolves for its own audience only |
| `services/notebook_tracking_gateway/tests/test_upload_audience.py` | Proof: tracking and upload handles do not cross |

## ADR-0015 dec 5, 6

The SQL proxy resolves the handle and opens the upstream session as the tenant's confined login identity.

| Code | Role |
|---|---|
| `services/notebook_sql_proxy` | The SQL proxy |

## ADR-0015 dec 7, 8

| Element | Form |
|---|---|
| Content | `nbcap:<handle>` entries (bound facts only), `nbcap-user:` and upload indexes, `nbcap-suspended:` markers, the `nbcap-audit` stream; no database or object-store secret |
| Eviction | `maxmemory-policy noeviction`; at `maxmemory` (compose 256mb, Helm 192mb) a write is refused and the mint or request fails |
| Watch | `IndustryFlowRedisMemoryHigh`: above 80% of the limit for 10m |

| Code | Role |
|---|---|
| `services/notebook_hub/capabilities.py::RedisCapabilityStore` | Hub store adapter |
| `services/ml_service/api/config.py` | Upload minter's store |
| `docker-compose.yml` | Redis without eviction |
| `deploy/helm/industryflow/templates/infra-redis.yaml` | Redis without eviction |
| `infrastructure/prometheus/rules/platform.yml::IndustryFlowRedisMemoryHigh` | Memory alert |
| `deploy/helm/industryflow/files/prometheus-rules.yml::IndustryFlowRedisMemoryHigh` | Memory alert (Helm) |
| `services/notebook_hub/tests/test_capabilities.py::test_store_holds_no_database_secret` | Proof: no secret in an entry |
| `services/ml_service/tests/test_upload_capability.py::test_the_record_holds_bound_facts_and_nothing_resembling_a_secret` | Proof: upload entry holds bound facts only |
| `tests/config_guards/test_capability_store_eviction.py` | Proof: no eviction, a limit, an alert |

## ADR-0018 dec 1

Configuration selects the Docker or the Kubernetes spawner (see Deployment profiles).

| Code | Role |
|---|---|
| `services/notebook_hub/jupyterhub_config.py` | Spawner selection |
| `docker-compose.yml` | Docker spawner deployment |
| `deploy/helm/industryflow` | Kubernetes spawner deployment |

## ADR-0018 dec 3, 4

| | Compose (DockerSpawner) | Kubernetes (KubeSpawner) |
|---|---|---|
| User | Images `USER 1000`; container `user: 1000:1000` | Images `USER 1000`; `uid` 1000, `runAsNonRoot` |
| Privilege | `cap_drop: ALL`, `no-new-privileges` | `allowPrivilegeEscalation: false`, capabilities dropped |
| Network | `industryflow-notebook-kernels`: hub, API gateway, SQL proxy, tracking gateway, cold broker, MinIO; not `internal` | Single-user egress NetworkPolicy |

| Code | Role |
|---|---|
| `services/notebook_hub/Dockerfile.authoring` | Non-root image |
| `services/notebook_hub/Dockerfile.analytics` | Non-root image |
| `services/notebook_hub/jupyterhub_config.py` | Per-spawner security settings |
| `docker-compose.yml` | Kernel network and its members |
| `deploy/helm/industryflow/templates/networkpolicy.yaml` | Single-user egress policy |
| `.github/workflows/compose-smoke.yml` | Proof: the kernel network reaches the mediators and nothing else |

## ADR-0018 dec 5

Compose runs no standalone Jupyter service; the hub's per-user environments are the only notebook
surface.

| Code | Role |
|---|---|
| `docker-compose.yml` | Notebook services: hub, SSO proxy, mediators only |

## ADR-0019 dec 1, 5, 6

The kernel reaches tracking only through the gateway, which namespaces every operation and presigns artifact transfers.

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway` | Tracking gateway |

## ADR-0019 dec 4

The hub sets `MLFLOW_TRACKING_TOKEN` to the `tracking` handle, so the MLflow client sends it as
`Authorization: Bearer`. The gateway resolves the bearer in the capability store for the `tracking`
audience, or answers 401, and calls MLflow with its own client; the bearer is not forwarded.

| Code | Role |
|---|---|
| `services/notebook_hub/jupyterhub_config.py::bind_identity_and_profile` | Handle as the MLflow token |
| `services/notebook_tracking_gateway/gateway.py::_bearer` | Bearer read |
| `services/notebook_tracking_gateway/policy.py::resolve_tracking_binding` | Store lookup |
| `services/notebook_tracking_gateway/gateway.py::build_app` | Proxy to MLflow |

## ADR-0019 dec 7

Trusted platform code reads the tracking server directly.

| Code | Role |
|---|---|
| `services/ml_service/api/routers` | Platform read paths |

## ADR-0020 dec 1, 2

An authoring server mounts one per-user volume at its work subdirectory; idle servers are culled.

| Code | Role |
|---|---|
| `services/notebook_hub/jupyterhub_config.py` | Volume mount and culler |
| `services/notebook_hub/Dockerfile.authoring` | Authoring image home layout |

## ADR-0020 dec 6

The hub removes a deleted tenant's volumes and records the reclaim.

| Code | Role |
|---|---|
| `services/notebook_hub/reclaim.py` | Volume reclaim |
| `services/notebook_hub/tests/test_reclaim_docker_live.py` | Removal against a real Docker daemon |

## ADR-0042 dec 1-3, 5

Each plane appends its event to the stream in the same step as the store change.

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/capability_audit.py` | Event contract |

## ADR-0042 dec 4, 6

One writer drains the stream into the audit table through a privileged function.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/31-capability-audit.sql` | Table and its writer |

## ADR-0042 dec 7

Every append trims the stream to about 100 000 entries; the writer deletes what it has written. The
backlog and its alerts: [capability-use record](#capability-use-record).

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/capability_audit.py::STREAM_MAXLEN` | Stream bound |
| `services/api_gateway/messaging/capability_audit_drain.py::BACKLOG` | Backlog gauge |
| `infrastructure/prometheus/rules/platform.yml::IndustryFlowCapabilityAuditBacklog` | Lag alert |
| `infrastructure/prometheus/rules/platform.yml::IndustryFlowCapabilityEventsDropped` | Dropped-event alert |
| `deploy/helm/industryflow/files/prometheus-rules.yml` | Same alerts (Helm) |
| `services/api_gateway/tests/test_capability_audit_drain.py::test_the_backlog_is_what_waits_in_the_stream` | Proof: backlog measure |
| `infrastructure/prometheus/rules/tests/platform_test.yml` | Proof: lag alert timing |

## ADR-0042 dec 8, 9

| Event | Window | Key |
|---|---|---|
| Refusal, SQL session open or close, of a real entry | One per `handle_id`, plane, kind and reason per 5 minutes; the rest counted into the next record's `suppressed` | `nbcap-audit-repeat:`, `nbcap-audit-repeats:` |
| Refusal of a value naming no entry | One per plane and digest per hour | `nbcap-audit-refused:` |
| Same, per source | At most 60 per plane and source address per minute; requests are not throttled | `nbcap-audit-source:` |

The source address is `X-Real-IP` when the peer is in `EDGE_PROXY_PEERS`, the connecting peer otherwise.

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/capability_audit.py::REPEAT_SCRIPT` | Per-entry window and count |
| `libs/industryflow_contracts/industryflow_contracts/capability_audit.py::UNKNOWN_REFUSAL_SCRIPT` | Per-value and per-source windows |
| `libs/industryflow_contracts/industryflow_contracts/capability_audit.py::client_source` | Source address |
| `services/api_gateway/capability_auth.py::record_resolution` | Data-API plane |
| `services/notebook_sql_proxy/binding.py::record_session` | SQL sessions |
| `services/notebook_cold_broker/policy.py::record_resolution` | Cold plane |
| `services/notebook_tracking_gateway/gateway.py::record_resolution` | Tracking and upload planes |
| `services/api_gateway/dependencies.py::EDGE_PEERS` | Edge peers, API gateway |
| `deploy/helm/industryflow/templates/notebook-tracking-gateway.yaml` | Edge peers, tracking gateway |
| `infrastructure/timescaledb/init-scripts/31-capability-audit.sql` | `suppressed` column |
| `tests/e2e/test_capability_use.py::test_a_kernel_repeating_a_refused_handle_leaves_one_row` | Proof: one row for repeats |
| `services/notebook_tracking_gateway/tests/test_capability_record.py::test_an_unknown_handle_is_recorded_once_by_its_digest_never_the_handle` | Proof: unknown value once |

## ADR-0042 dec 10

The writer calls `expire_capability_use(CAPABILITY_AUDIT_RETENTION_DAYS)` hourly (default 365); the
function refuses fewer than 30 days, whoever calls it.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/31-capability-audit.sql::expire_capability_use` | Expiry with the floor |
| `services/api_gateway/messaging/capability_audit_drain.py::expire` | Hourly call |
| `deploy/helm/industryflow/values.yaml` | Retention setting |
| `docker-compose.yml` | Retention setting |
| `infrastructure/timescaledb/tests/test_capability_audit.py::test_retention_removes_only_what_is_older_than_its_period_and_never_under_the_floor` | Proof: period and floor |
