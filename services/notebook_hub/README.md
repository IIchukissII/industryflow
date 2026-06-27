<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# notebook_hub

The notebook hub that spawns per-user, isolated notebook environments (ADR-0011), authenticated
from the platform session (ADR-0014) and bound to the caller's tenant (ADR-0012).

## Contents

- **`identity.py`** — pure, unit-tested decision logic: parse the verified identity the trusted
  proxy forwards, choose the spawn profile from the role (ADR-0011 dec 5), and derive the pod
  labels and identity-only environment. Holds no data credential.
- **`jupyterhub_config.py`** — *reference* JupyterHub/KubeSpawner wiring around `identity.py`:
  the header-trusting authenticator (ADR-0014), the role-driven `pre_spawn_hook`, and pod
  containment (non-root, dropped capabilities, resource limits).
- **`tests/`** — unit tests for `identity.py` (run in the `unit-tests` CI workflow).

## Status

Phase-2, partial. **Verified:** the identity/profile logic (`identity.py` + tests). **Not yet
cluster-validated:** `jupyterhub_config.py` (the JupyterHub API wiring) and the hub
Deployment/proxy integration into the Helm chart. The single-user pod isolation NetworkPolicy
ships in the chart behind `notebookHub.enabled` (default off).

## Deferred (later phases)

- Hub Deployment, the configurable HTTP proxy, RBAC, and the SSO reverse-proxy that forwards
  the verified identity (ADR-0014 handoff contract).
- Per-session capability minting and the SQL proxy (ADR-0012) — the kernel's data credentials.
- The single-user notebook images (operator/author) and per-user persistence.

## Develop

`identity.py` is pure stdlib; the tests need only pytest:

```bash
pip install -r requirements-dev.txt
pytest tests/
```
