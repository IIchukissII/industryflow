<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0020 — Notebook per-user work persistence

- **Status:** Accepted
- **Date:** 2026-06-28; changed 2026-10-21
- **Parent:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md)
- **Related:** [ADR-0018](ADR-0018-notebook-hub-spawner-portability.md) (spawner parity), [ADR-0033](ADR-0033-tenant-lifecycle.md) (tenant deletion, dec 6),
  [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (ephemeral capabilities)

## Context

Notebook environments are ephemeral and reclaimed when idle; without storage an author loses work on
every restart or image upgrade. The analytics profile authors nothing. Durable work must not persist
credentials, make compute long-lived or cross tenants.

## Decision

1. **An authoring environment gets one durable per-user volume at a work subdirectory, not the whole
   home.** The rest of the home, examples included, comes fresh from the image; analytics environments
   get no storage.
2. **Compute stays ephemeral and is reclaimed when idle.** An idle-culler stops idle servers with a
   token scoped to listing users, reading activity and stopping servers.
3. **Capabilities are never persisted.** They live only in the process and are re-minted each spawn.
4. **No cross-tenant human administration.** Lifecycle is automatic or self-service, and the platform
   admin role, a tenant role, is not a hub administrator.
5. **Parity across spawners for the authoring profile.** A named volume on a Docker host, a claim on
   Kubernetes, at the same subdirectory, which the authoring image creates owned by the non-root user.
6. **The hub reclaims a deleted user's volume itself.** It learns of the deletion from the platform's
   durable deletion record (ADR-0033 dec 4), so no hub credential leaves the hub.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Mount the whole home | The volume freezes image content per user |
| 1 | Stay ephemeral; users export work | Unusable for authoring |
| 1 | A work volume for every user, analytics profile included | Durable storage for a profile that authors nothing, leaving volumes to reclaim |
| 2 | A standing hub admin or service token | An ambient credential that crosses tenants |
| 4 | The platform admin role mapped to a hub administrator | A tenant role would reach other tenants' environments |
| 6 | The tenant-delete job removes volumes through the container platform | Gives it privilege over storage the hub owns and names |

## Consequences

**Positive**
- Authored work survives restarts, idle reclaim and upgrades, so idle servers are reclaimed freely.

**Negative**
- Volumes accumulate per authoring user; a Docker-host volume has no size cap, a Kubernetes claim is
  capped by its requested size.
- Persisted notebooks do not record who executed what.

## Deferred

| Open question | Settled by |
|---|---|
| Reclaiming the volume of a user removed without deleting the tenant | First storage pressure |
| A size cap on Docker-host volumes | First volume that fills its host |
| An execution audit trail | ADR-0011's deferral |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): paths, naming, culler scopes.
- `services/notebook_hub/jupyterhub_config.py`, `services/notebook_hub/Dockerfile.authoring`.
