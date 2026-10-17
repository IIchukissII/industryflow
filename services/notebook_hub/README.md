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
| `identity.py` | Parses the verified identity headers (`X-IF-User`, `X-IF-Company-Id`, `X-IF-Role`), selects the profile from the role, derives pod labels and the identity-only environment, reconciles a hub login with the forwarded identity |
| `capabilities.py` | Capability handle mint / resolve / revoke and the per-server index `nbcap-user:<user>` ([ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md)) |
| `jupyterhub_config.py` | Header-trusting authenticator with `refresh_user`, `pre_spawn_hook`, `post_stop_hook`, polling spawner subclass, idle culler, containment, spawner selection ([ADR-0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md)) |
| `sso-nginx.conf` | SSO reverse proxy: validates the platform session against api-gateway, overwrites the identity headers and sets the proxy secret on hub routes |
| `Dockerfile` | Hub image (JupyterHub + configurable-http-proxy), uid 10001, ports 8000/8081 |
| `Dockerfile.authoring` | Authoring single-user image: JupyterLab, the `industryflow` client with `[sql,cold]`; build context `clients/python` |
| `Dockerfile.analytics` | Analytics single-user image: Voila, client without `[sql]`; build context `clients/python` |
| `tests/` | `test_identity.py`, `test_capabilities.py`, `test_jupyterhub_config.py` (loads the config under both spawners) |

## Settings

| Name | Default | Meaning |
|---|---|---|
| `NOTEBOOK_SPAWNER` | `kube` | `kube` (KubeSpawner) or `docker` (DockerSpawner) |
| `NOTEBOOK_BASE_URL` | `/` | Hub path prefix (`/jupyter/` when embedded) |
| `REDIS_URL` | required | Capability store |
| `NOTEBOOK_SSO_PROXY_SECRET` | required | Shared with the SSO proxy; ≥ 32 alphanumeric characters; the hub does not start without it |
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
