<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# notebook_hub

The notebook hub that spawns per-user, isolated notebook environments (ADR-0011), authenticated
from the platform session (ADR-0014) and bound to the caller's tenant (ADR-0012).

## Contents

- **`identity.py`** — pure, unit-tested decision logic: parse the verified identity the trusted
  proxy forwards, choose the spawn profile from the role (ADR-0011 dec 5), derive the pod labels
  and identity-only environment, and reconcile an established hub login with the identity
  forwarded now. Holds no data credential.
- **`capabilities.py`** — pure, unit-tested capability logic (ADR-0015): mint, resolve, revoke, and
  the per-server lifecycle (a per-user index of minted handles in the store, so a server's handles
  can be refreshed while it is healthy and revoked when it stops, is respawned, or its user logs
  out of the platform).
- **`jupyterhub_config.py`** — the JupyterHub wiring around both: the header-trusting
  authenticator (ADR-0014) with `refresh_user`, the role-driven `pre_spawn_hook` that mints the
  kernel's handles, the capability lifecycle (`post_stop_hook`, and a spawner subclass whose poll
  refreshes the handles), the idle culler, and pod containment. The spawner is a deployment profile
  (ADR-0018): DockerSpawner on Compose, KubeSpawner on Kubernetes.
- **`tests/`** — run in the `unit-tests` CI workflow; `test_jupyterhub_config.py` loads the real
  config under both spawner profiles and drives its hooks against a fake store.

## Session and capability lifecycle

| Event | What happens |
|---|---|
| Spawn | handles minted for the server (API; authoring also SQL, tracking, cold), recorded in `nbcap-user:<user>`; whatever an earlier run of the server still held is revoked first |
| Server healthy (every spawner poll) | its handles' lifetime is extended (`NOTEBOOK_CAPABILITY_TTL_SECONDS`, default 900) |
| Server stops (user, culler, hub logout) | `post_stop_hook` revokes its handles |
| Platform logout | the frontend logs out of the hub first (`shutdown_on_logout` stops the server); the platform's `/auth/logout` revokes all of the user's handles; a server whose handles were all revoked is stopped at its next poll |
| Hub request (every `NOTEBOOK_AUTH_REFRESH_SECONDS`, default 60, and before a spawn) | the hub login is compared with the identity the SSO proxy forwards: another user ends the login; the same user with another tenant or role ends it and stops their servers |

## Status

Built and validated on the Compose box (DockerSpawner). The KubeSpawner profile renders and lints
in the Helm chart but has not run on a cluster.

## Develop

```bash
pip install -r requirements-dev.txt
pytest tests/
```
