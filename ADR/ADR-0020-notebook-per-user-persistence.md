<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0020 — Notebook per-user work persistence (durable storage, ephemeral compute)

- **ID:** ADR-0020
- **Status:** Accepted
- **Date:** 2026-06-28
- **Project:** IndustryFlow
- **Parent:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (embedded notebooks — resolves its deferred "where authored notebooks live")
- **Companions:** [ADR-0018](ADR-0018-notebook-hub-spawner-portability.md) (spawner parity), [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (ephemeral capabilities)

## Context and problem

ADR-0011 spawns one **ephemeral** notebook environment per user, "reclaimed when idle" (dec 1, 7), and explicitly **deferred** *where authored notebooks live*. Built as deferred, the singleuser container is removed on stop with no storage, so a data scientist loses their notebooks every time their server restarts — including when the hub reclaims an idle server or a new image ships. That makes the authoring profile unusable for real work.

The open question: add durable storage **without** weakening the security posture — the kernel must still hold no persisted data credential, the compute must still be ephemeral and idle-reclaimed, and tenants must stay isolated.

## Decision drivers

- **Ephemeral *compute*, durable *work* are separable.** ADR-0011's ephemerality is about the execution environment (reclaimed when idle, rebuilt from a fresh image); it does not require throwing away the user's files.
- **Capabilities must remain ephemeral (ADR-0015).** Data credentials are re-minted per spawn and never written to durable storage.
- **Spawner parity (ADR-0018).** Persistence must exist for both DockerSpawner (Compose) and KubeSpawner (k8s), differing only in the storage primitive.
- **Tenant isolation (ADR-0011).** No mechanism here may grant one tenant access to another's environment or files.

## Decision

1. **Per-user durable storage for authored work, mounted at a `work/` subdirectory — not the whole home.** Each user gets one persistent volume mounted at `/home/jovyan/work`; their notebooks live there and survive restarts. The rest of the home (and the read-only `examples/` baked into the image, ADR-0011) stays **image-supplied and ephemeral**, so an immutable example or a stack upgrade is always the fresh image's, never a stale copy frozen in a volume. The storage is **per user**, named/claimed by the (already tenant-bound) username.

2. **The compute stays ephemeral and idle-reclaimed.** The container/pod is still removed on stop (`remove=True` / pod deletion); only the `work/` volume persists. ADR-0011 dec 1/7's "reclaimed when idle" is made concrete with a **`jupyterhub-idle-culler` service**: it stops servers idle past a timeout, holding a **minimal scoped token** (`list:users`, `read:users:activity`, `read:servers`, `delete:servers`) — least-privilege, not an ambient admin credential. Reclaiming an idle server now costs nothing: the work is safe on the volume and the next spawn restores it from a fresh image.

3. **Capabilities are never persisted.** The SQL / API / tracking capability handles (ADR-0015) are injected into the environment at spawn and live only in the process; nothing writes them to `work/`. A restored volume carries notebooks, not credentials — the kernel re-authenticates with freshly-minted handles every spawn.

4. **No cross-tenant human administration.** Server lifecycle is automatic (idle-culler) and self-service (a user starts/stops *their own* server through the hub UI). The platform `admin` role is a **tenant** role, so it is deliberately **not** mapped to a JupyterHub admin — that would grant cross-tenant access to other tenants' environments, violating ADR-0011's isolation. Operating on another tenant's server is not a capability the system grants anyone.

5. **Parity across spawners (ADR-0018).** DockerSpawner uses a per-user **named volume**; KubeSpawner uses a per-user **PVC** (auto-ensured). Both mount at `/home/jovyan/work`; the images create that directory owned by the non-root uid so a fresh volume initialises writable. Everything else (capability minting, profile, containment) is unchanged and spawner-agnostic.

## Alternatives considered

- **Mount the whole home (`/home/jovyan`) as the volume.** *Rejected:* a fresh named volume seeds from the image once, then freezes — the read-only example and any future stack content would go stale per user, and ephemerality would erode into "whatever each user's home drifted to." The `work/` subpath keeps image-supplied content always-fresh and user work durable.
- **A static hub admin / service token for operators to manage servers.** *Rejected:* an ambient long-lived credential is exactly what ADR-0015's capability model avoids, and a hub admin crosses the tenant boundary. The idle-culler (scoped, automatic) covers the lifecycle need without either flaw.
- **Keep ephemeral, document "export your work."** *Rejected:* unusable for authoring; pushes a platform responsibility onto users.

## Consequences

### Positive
- Authored notebooks survive restarts, idle-reclaim, and image upgrades; idle servers can be reclaimed freely (work is safe), so the ephemeral-compute posture is *strengthened*, not weakened.
- The security model is intact: ephemeral compute, freshly-minted non-persisted capabilities, per-user isolation, no cross-tenant administration.
- Same behaviour on Compose and k8s (ADR-0018).

### Negative
- Per-user volumes/PVCs accumulate and need a lifecycle (quota, reclaim on offboarding) — see deferred.
- A user can fill their `work/` volume; per-user storage quota is a deferred operational control.
- Persisted notebooks raise the audit question ADR-0011 already deferred (who ran what); still deferred.

## Deferred decisions

- **Storage quota + reclamation.** Per-user volume size caps and the offboarding/GC policy (when a departed user's volume is reclaimed) are operational config, deferred.
- **Execution audit trail.** Inherited from ADR-0011; persisted work does not by itself record who executed what against which tenant.
- **Idle timeout + cull cadence values.** The exact timeout/period are env-tunable configuration, not architecture.

## References
- ADR-0011 (embedded notebooks; ephemerality, deferred persistence), ADR-0015 (capabilities), ADR-0018 (spawner parity).
- `services/notebook_hub/jupyterhub_config.py` (persistence + idle-culler wiring), `Dockerfile.authoring` / `Dockerfile.analytics` (the `work/` dir).
