<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# notebook_hub

The notebook hub: spawns per-user notebook environments ([ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md)), signed in from the platform session ([ADR-0014](../../ADR/ADR-0014-notebook-hub-single-sign-on.md)) and bound to the caller's tenant ([ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md)).

## Files

| File | Content |
|---|---|
| `identity.py` | Parses the verified identity headers (`X-IF-User`, `X-IF-Company-Id`, `X-IF-Role`), selects the profile from the role, derives pod labels and the identity-only environment, reconciles a hub login with the forwarded identity |
| `capabilities.py` | Capability handle mint / resolve / revoke and the per-server index `nbcap-user:<user>` ([ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md)) |
| `jupyterhub_config.py` | Header-trusting authenticator with `refresh_user`, `pre_spawn_hook`, `post_stop_hook`, polling spawner subclass, idle culler, containment, spawner selection ([ADR-0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md)) |
| `sso-nginx.conf` | SSO reverse proxy: validates the platform session against api-gateway and overwrites the identity headers before the hub |
| `Dockerfile` | Hub image (JupyterHub + configurable-http-proxy), uid 10001, ports 8000/8081 |
| `Dockerfile.authoring` | Authoring single-user image: JupyterLab, the `industryflow` client with `[sql]`; build context `clients/python` |
| `Dockerfile.analytics` | Analytics single-user image: Voila, client without `[sql]`; build context `clients/python` |
| `tests/` | `test_identity.py`, `test_capabilities.py`, `test_jupyterhub_config.py` (loads the config under both spawners) |

## Profiles

| Role → profile | Image env var | Landing | Capability handles | Work volume |
|---|---|---|---|---|
| authoring | `NOTEBOOK_IMAGE_AUTHORING` | JupyterLab | API; SQL; tracking if `INDUSTRYFLOW_TRACKING_URL`; cold if `INDUSTRYFLOW_COLD_BROKER_URL` | `NOTEBOOK_VOLUME_TEMPLATE` at `<home>/work` ([ADR-0020](../../ADR/ADR-0020-notebook-per-user-persistence.md)) |
| analytics | `NOTEBOOK_IMAGE_ANALYTICS` | `/voila` | API | none |

Either image falls back to `NOTEBOOK_IMAGE`.

## Settings

| Env var | Default | Meaning |
|---|---|---|
| `NOTEBOOK_SPAWNER` | `kube` | `kube` (KubeSpawner) or `docker` (DockerSpawner) |
| `NOTEBOOK_BASE_URL` | `/` | Hub path prefix (`/jupyter/` when embedded) |
| `REDIS_URL` | required | Capability store |
| `NOTEBOOK_CAPABILITY_TTL_SECONDS` | `900` | Handle lifetime, extended on every spawner poll |
| `NOTEBOOK_AUTH_REFRESH_SECONDS` | `60` | Hub login re-check interval |
| `NOTEBOOK_IDLE_TIMEOUT_SECONDS` | `3600` | Idle culler timeout |
| `NOTEBOOK_CULL_EVERY_SECONDS` | `300` | Idle culler interval |
| `NOTEBOOK_HOME` | `/home/jovyan` | Single-user home |
| `NOTEBOOK_VOLUME_TEMPLATE` | `industryflow-nb-{username}` | Per-user volume / PVC name |
| `NOTEBOOK_STORAGE_CAPACITY` | `2Gi` | PVC size (kube) |
| `NOTEBOOK_NETWORK` | required (docker) | Docker network shared with hub and proxy |
| `HUB_CONNECT_IP` | `notebook-hub` | Hub address for single-user containers (docker) |
| `HUB_PUBLIC_URL` | `http://0.0.0.0:8000` | Hub public bind (docker) |
| `INDUSTRYFLOW_API_URL` | unset | Passed to kernels |
| `INDUSTRYFLOW_SQL_PROXY_URL` | unset | Passed to authoring kernels |
| `INDUSTRYFLOW_TRACKING_URL` | unset | Passed to authoring kernels as `MLFLOW_TRACKING_URI`; enables the tracking handle |
| `INDUSTRYFLOW_COLD_BROKER_URL` | unset | Passed to authoring kernels; enables the cold handle |

Kernel environment: `INDUSTRYFLOW_API_CAPABILITY`, `INDUSTRYFLOW_SQL_CAPABILITY`,
`INDUSTRYFLOW_TRACKING_CAPABILITY` (also `MLFLOW_TRACKING_TOKEN`), `INDUSTRYFLOW_COLD_CAPABILITY`.

## Session and capability lifecycle

| Event | Effect |
|---|---|
| Spawn | Handles of an earlier run of the server are revoked; new handles are minted and recorded in `nbcap-user:<user>` |
| Spawner poll, server healthy | Handle lifetime extended by `NOTEBOOK_CAPABILITY_TTL_SECONDS` |
| Server stops (user, culler, hub logout) | `post_stop_hook` revokes its handles |
| Platform logout | Frontend logs out of the hub first (`shutdown_on_logout` stops the server); `/auth/logout` revokes all the user's handles; a server whose handles are all revoked stops at its next poll |
| Hub request after `NOTEBOOK_AUTH_REFRESH_SECONDS`, and before a spawn | Hub login compared with the forwarded identity: a different user ends the login; the same user with another tenant or role ends it and stops their servers |

## Status

| Spawner | State |
|---|---|
| DockerSpawner (Compose) | Runs |
| KubeSpawner (Helm) | Renders and lints; not run on a cluster |

## Commands

| Task | Command |
|---|---|
| Install test deps | `pip install -r requirements-dev.txt` |
| Unit tests (also in `unit-tests.yml`) | `pytest tests/` |
