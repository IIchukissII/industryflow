<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0018 — Notebook hub spawner portability

- **Status:** Accepted
- **Date:** 2026-06-28; changed 2026-10-17
- **Parent:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md)
- **Related:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (deployment shapes, per-pod
  egress), [ADR-0014](ADR-0014-notebook-hub-single-sign-on.md) (SSO),
  [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (capabilities, SQL proxy),
  [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (smoke gate)

## Context

The platform runs on a single Docker host and on Kubernetes. Notebook tenant isolation comes from SSO
identity, spawner-minted single-tenant handles and per-tenant database grants, none of which depend on
how the per-user environment is placed. A per-pod egress allowlist has no exact equivalent on a single
Docker host.

## Decision

1. **The spawner is a deployment profile selected by configuration.** The hub places a per-user pod on
   Kubernetes and a per-user container on a Docker host.
2. **Every isolation invariant of ADR-0011 holds under both spawners and is enforced above them.** The
   spawner only places the environment and injects the handles the layers above mint.
3. **Kernels run non-root on both paths.** No privilege escalation; the images declare a non-root user.
4. **Network containment differs per platform.** On Kubernetes a per-pod egress policy allowlists the
   endpoints a kernel may reach; on a Docker host the kernel joins a dedicated network holding only the
   hub, the trusted mediators and the object store, which keeps a route off the host because a
   pre-signed address may name the public edge (ADR-0030).
5. **The standalone shared Jupyter is retired, not hardened.** Any local developer notebook is
   non-root, authenticated, loopback-bound and never a product surface.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Kubernetes spawner only | The single-host deployment would have no conforming notebooks |
| 1 | Docker spawner only | Drops the strongest egress containment |
| 1 | Standing per-tenant notebook deployments | Contradicts the ephemeral, on-demand posture |
| 4 | On a Docker host, a dedicated network with no route off the host | A pre-signed address that names the public edge would be unreachable from the kernel |
| 5 | Harden the shared Jupyter | No per-user identity or tenant scoping |

## Consequences

**Positive**
- Tenant-isolated, non-root notebooks on both shapes from one hub configuration and one isolation design.

**Negative**
- Two spawner paths and manifest sets must stay aligned.
- On a Docker host, egress outside the platform is bounded by the host alone; allowlist-grade egress
  needs Kubernetes.
- The Kubernetes profile is rendered and linted in CI but has not run on a cluster; the Docker-host
  kernel network is checked by the smoke gate.

## Deferred

| Open question | Settled by |
|---|---|
| Tighter Docker-host egress (no route off the host, firewall rules, outbound proxy) | First deployment needing allowlist-grade egress without Kubernetes |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): the dual-spawner design.
- `services/notebook_hub/jupyterhub_config.py`, `docker-compose.yml`, `deploy/helm/industryflow/`.
