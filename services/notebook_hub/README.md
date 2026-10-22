<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Notebook hub

The notebook hub service: spawns per-user notebook environments, signs users in from the platform
session and mints their capability handles. Profiles, handle lifecycle and deployment profiles:
[notebooks](../../docs/architecture/notebooks.md). Unit tests (`tests/`, in `unit-tests.yml`):
[getting started](../../docs/getting-started.md#tests).

## Files

| File | Content |
|---|---|
| `identity.py` | Parses the verified identity headers (`X-IF-User`, `X-IF-Company-Id`, `X-IF-Role`), validates the authoring-role setting, selects the profile from the role, derives pod labels and the identity-only environment, reconciles a hub login with the forwarded identity |
| `capabilities.py` | Capability handle mint / resolve / revoke and the per-server index `nbcap-user:<user>` ([ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md)) |
| `reclaim.py` | `volume-reclaim` service: deletes a deleted tenant's users from the hub and records `notebook_volumes` ([ADR-0020](../../ADR/ADR-0020-notebook-per-user-persistence.md) dec 6) |
| `jupyterhub_config.py` | Header-trusting authenticator with `refresh_user`, `pre_spawn_hook`, `post_stop_hook`, polling spawner subclass, volume removal on user deletion, idle culler, volume-reclaim service, containment, spawner selection ([ADR-0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md)) |
| `sso-nginx.conf` | SSO reverse proxy: validates the platform session against api-gateway, overwrites the identity headers and sets the proxy secret on hub routes |
| `Dockerfile` | Hub image (JupyterHub + configurable-http-proxy), uid 10001, ports 8000/8081 |
| `Dockerfile.authoring` | Authoring single-user image: JupyterLab, the `industryflow` client with `[sql,cold]`; build context `clients/python` |
| `Dockerfile.analytics` | Analytics single-user image: Voila, client without `[sql]`; build context `clients/python` |
| `tests/` | `test_identity.py`, `test_capabilities.py`, `test_reclaim.py`, `test_jupyterhub_config.py` (loads the config under both spawners) |

## Settings

| Name | Default | Meaning |
|---|---|---|
| `NOTEBOOK_SPAWNER` | `kube` | `kube` (KubeSpawner) or `docker` (DockerSpawner) |
| `NOTEBOOK_BASE_URL` | `/` | Hub path prefix (`/jupyter/` when embedded) |
| `REDIS_URL` | required | Capability store |
| `NOTEBOOK_SSO_PROXY_SECRET` | required | Shared with the SSO proxy; ≥ 32 alphanumeric characters; the hub does not start without it |
| `NOTEBOOK_AUTHORING_ROLES` | `admin,engineer` | Comma-separated platform roles (`admin`, `engineer`, `observer`) that get the authoring profile; any other role gets analytics; an empty list or an unknown role stops the hub at start |
| `NOTEBOOK_CAPABILITY_TTL_SECONDS` | `900` | Handle lifetime, extended on every spawner poll |
| `NOTEBOOK_AUTH_REFRESH_SECONDS` | `60` | Hub login re-check interval |
| `NOTEBOOK_IDLE_TIMEOUT_SECONDS` | `3600` | Idle culler timeout |
| `NOTEBOOK_CULL_EVERY_SECONDS` | `300` | Idle culler interval |
| `NOTEBOOK_HOME` | `/home/jovyan` | Single-user home |
| `NOTEBOOK_VOLUME_TEMPLATE` | `industryflow-nb-{username}` | Per-user volume / PVC name |
| `NOTEBOOK_STORAGE_CAPACITY` | `2Gi` | PVC size (kube) |
| `NOTEBOOK_NETWORK` | required (docker) | Docker network shared with hub and proxy |
| `HUB_CONNECT_IP` | `notebook-hub` (docker), unset (kube) | Hub API address for chp and single-user servers |
| `HUB_PUBLIC_URL` | `http://0.0.0.0:8000` | chp public bind (docker); compose sets `http://notebook-hub-public:8000`, an alias only the platform network resolves |
| `CONFIGPROXY_API_URL` | unset | kube: API of the sibling chp; when set the hub starts no chp |
| `INDUSTRYFLOW_API_URL` | unset | Passed to kernels |
| `INDUSTRYFLOW_SQL_PROXY_URL` | unset | Passed to authoring kernels |
| `INDUSTRYFLOW_TRACKING_URL` | unset | Passed to authoring kernels as `MLFLOW_TRACKING_URI`; enables the tracking handle |
| `INDUSTRYFLOW_COLD_BROKER_URL` | unset | Passed to authoring kernels; enables the cold handle |
| `NOTEBOOK_RECLAIM_DB_PASSWORD` | unset (no `volume-reclaim`) | `notebook_hub_reclaim_user` password |
| `NOTEBOOK_RECLAIM_DB_HOST`, `_PORT`, `_NAME`, `_USER` | required, `5432`, `industryflow`, `notebook_hub_reclaim_user` | Database of the deletion record |
| `NOTEBOOK_RECLAIM_DB_SSLMODE`, `_SSLROOTCERT` | `verify-full`, `/etc/ssl/industryflow/ca.crt` | Its TLS |
| `NOTEBOOK_RECLAIM_EVERY_SECONDS` | `300` | Reclaim pass interval |
