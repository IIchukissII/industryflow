<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0018: Notebook hub spawner portability (compose and Kubernetes)

- **ID:** ADR-0018
- **Status:** Accepted
- **Date:** 2026-06-28
- **Project:** IndustryFlow
- **Parent:** ADR-0011 (embedded notebooks — the shape this refines)
- **Companions:** ADR-0009 (Kubernetes deployment and packaging), ADR-0014 (notebook hub SSO), ADR-0015 (capability minting + SQL proxy)
- **Related:** ADR-0001 (self-hosted vs managed deployment), ADR-0002 (network-boundary posture)

## Context and problem

ADR-0011 decided that analytics and experimentation are delivered as per-user, isolated,
on-demand notebook environments behind the existing front door, with tenant isolation enforced by
database privilege rather than by `search_path` discipline. Its decision 1 was careful to record
the *property* — one isolated environment per user — and named "a JupyterHub-style hub with a
per-user pod spawner" only as the mechanism. The implementation that followed, however, bound the
hub to **KubeSpawner**: per-user *pods*, scheduled by Kubernetes, contained by a single-user-pod
egress **NetworkPolicy** (ADR-0009, ADR-0011 dec 7). The hub configuration, the Helm
`notebookHub` stack, and the cluster-bound TODO in the design doc all assume Kubernetes.

That assumption now collides with how the platform actually runs. The primary live deployment is
**Docker Compose on a single host** (the self-hosted model of ADR-0001); Kubernetes is supported
but not the only — or, today, the validated — target. As written, the notebook capability cannot
exist anywhere but on a cluster, so the one place the platform is actually operated has no path to
multi-tenant notebooks at all. What it has instead is the very thing ADR-0011 set out to replace:
a standalone Jupyter container running as **root**, with authentication disabled, holding the
shared ML-service database credentials (ADR-0011 alternative A). The gap between the decided
architecture and the deployable reality is filled by exactly the anti-pattern the architecture
exists to remove.

The question is whether per-user, tenant-isolated notebooks are inherently a Kubernetes feature,
or whether the isolation model ADR-0011/0012/0014/0015 built is independent of *how* the per-user
environment is placed — and if so, how to record that so a compose deployment is a conforming
realization rather than a divergence.

## Decision drivers

- **The isolation model lives above the spawner.** Tenant isolation for notebooks is produced by
  a per-tenant read-only database role (ADR-0011 dec 3, ADR-0012), by the kernel holding only
  opaque, single-tenant, revocable capability handles instead of credentials (ADR-0015), and by
  SSO that hands the spawner a verified identity to mint against (ADR-0014). None of these depend
  on Kubernetes; they depend on "the spawner" as an abstract minting authority — which is exactly
  how ADR-0014 and ADR-0015 already phrase it.
- **The deployable reality must conform, not be excepted.** A decision that can only be honoured
  on infrastructure the platform does not run is not honoured. The compose deployment needs a
  conforming notebook story so the root, auth-disabled, shared-credential shim can be retired
  rather than indefinitely tolerated.
- **Containment is not equally strong everywhere, and that must be stated, not hidden.** The
  precise egress allowlist of a single-user-pod NetworkPolicy (ADR-0009) has no exact equivalent
  on a single Docker host. An honest architecture names which guarantees are platform-specific
  rather than pretending parity.
- **Two spawners is a configuration concern, not a redesign.** JupyterHub treats the spawner as a
  pluggable class. Supporting KubeSpawner and DockerSpawner is a branch in hub configuration over
  shared identity, capability, and grant machinery — not a second notebook architecture.

## Decision

1. **The per-user-environment spawner is a deployment profile, selected by configuration — not an
   architectural fixture.** The hub backend remains a JupyterHub-style hub (ADR-0011 dec 1). Which
   spawner it uses is chosen per deployment: **KubeSpawner** (a per-user *pod*) on Kubernetes, and
   **DockerSpawner** (a per-user *container* on a Docker host) on Compose / single-host. This makes
   explicit the abstraction ADR-0011 dec 1 already intended ("one isolated environment per user …
   not any particular product's feature set") and names its two supported realizations.

2. **Every isolation invariant of ADR-0011 is binding under both spawners and is enforced above the
   spawner.** One ephemeral environment per user (dec 1); no ambient, tenant-crossing credential in
   the kernel (dec 2); tenant isolation by database privilege via a `tenant_reader_<uuid>` role
   (dec 3, ADR-0012); data reached only through the tenant-scoped API and, for authoring, a
   short-lived single-tenant read-only principal via the SQL proxy (dec 4/6, ADR-0015); SSO from
   the platform session with no second login (dec 8, ADR-0014). These are identical across spawners
   because they are produced by the SSO handoff, the capability store, and the database grants — all
   of which are spawner-independent. The spawner only *places* the environment and *injects* the
   handles the upper layers mint.

3. **Non-root execution is mandatory on both paths.** The kernel runs as a non-root user with no
   privilege escalation: KubeSpawner already enforces `runAsNonRoot`, a non-root uid, and dropped
   capabilities; DockerSpawner runs the notebook image's non-root `USER` with no `--allow-root`.
   The notebook images therefore declare a non-root user, and root execution is permitted on
   neither path.

4. **Network and resource containment (ADR-0011 dec 7) is realized per platform, and the
   realizations are not equivalent — this is acknowledged, not papered over.** On Kubernetes a
   single-user-pod **egress NetworkPolicy** gives a precise allowlist of the platform endpoints a
   kernel may reach (ADR-0009); this remains the strongest containment and the production posture.
   On a Docker host the environment runs on a **dedicated internal network**, holds **no ambient
   credentials**, and carries CPU/memory and concurrency limits — but Docker networking gives
   coarser egress control than a per-pod allowlist. Consequently the **compose profile's primary
   boundary is the per-user, non-root, no-credential, database-privilege-enforced isolation** of
   decisions 2–3, with network egress bounded at the host/network level rather than per
   environment. The compose profile targets single-host, test, and small deployments; deployments
   that require allowlist-grade egress containment use the Kubernetes profile.

5. **The standalone root Jupyter is retired by the hub, not hardened into permanence.** The shared,
   root, authentication-disabled, shared-credential Jupyter container (ADR-0011 alternative A) is
   replaced — on Compose by the DockerSpawner hub, on Kubernetes by the KubeSpawner hub. Should a
   purely local developer notebook be kept in the interim, it must be non-root, authenticated, and
   bound to loopback; it remains an out-of-band developer tool with no per-user identity and no
   multi-tenant path, exactly as ADR-0011 alternative A allows, and it is never a product surface.

## Alternatives considered

**A. Keep KubeSpawner only; require a cluster for any multi-tenant notebooks.** *Rejected:* it
leaves the platform's actual live deployment (Compose, ADR-0001) with no conforming notebook
capability, so the root/shared-credential shim persists indefinitely. The isolation model does not
in fact require Kubernetes — it requires a spawner, a capability store, and per-tenant grants — so
the cluster requirement is incidental, not essential.

**B. Harden the standalone shared Jupyter (non-root + a login token) and call it done.**
*Rejected:* ADR-0011 alternative A already rejected this surface. Non-root and a shared token give
no per-user identity and no tenant scoping; it is still one environment with one reach for everyone.
It is acceptable only as a local developer tool (decision 5), never as the multi-tenant answer.

**C. DockerSpawner only; drop KubeSpawner.** *Rejected:* the single-user-pod egress NetworkPolicy
is the strongest network containment the platform has (ADR-0009) and the production target. Pivoting
away from it to simplify would weaken the very posture ADR-0011 dec 7 demands for its
highest-privilege surface.

**D. A bespoke per-tenant notebook deployment per host.** *Rejected for the same reasons as
ADR-0011 alternative E:* standing per-tenant fleets contradict the ephemeral, on-demand,
quota-bounded posture and add operational burden, now compounded across hosts.

## Consequences

### Positive

- Multi-tenant, per-user, non-root notebooks become a **conforming capability on the deployment the
  platform actually runs** (Compose), not only on a cluster — so the root/shared-credential shim can
  finally be retired (decision 5).
- One isolation design serves both deployments: SSO, capability minting, the SQL proxy, and the
  `tenant_reader` grants are written once and reused under either spawner (decision 2), so the
  hardest, security-critical parts are not duplicated.
- The architecture is **honest about non-parity**: it records that allowlist-grade egress
  containment is a Kubernetes property and that the compose profile leans on credential and
  database-privilege isolation instead (decision 4), so an operator chooses with eyes open.

### Negative

- Two spawner code paths and two sets of deployment manifests (a compose hub + DockerSpawner; the
  Helm `notebookHub` + KubeSpawner) must be maintained and kept behaviourally aligned.
- The compose profile's network egress containment is **weaker** than the Kubernetes profile's
  (decision 4); deployments with strict egress requirements must use Kubernetes, and that trade-off
  has to be communicated, not assumed away.
- Building the compose profile still requires the pieces the cluster path also needs — the notebook
  images and the SQL proxy's Postgres-wire backend — so spawner portability enables the compose
  deployment but does not by itself complete it.

## Deferred decisions

- **Implementation of both profiles.** The cluster path is tracked as cluster-bound work
  (the embedded-notebooks issue); the compose path — a hub service with DockerSpawner, the
  notebook images, and the SQL-proxy wire backend — is its sibling. This ADR records the design they
  conform to, not the build.
- **Compose egress hardening.** Whether and how far the compose profile tightens egress (a
  dedicated internal network, host firewall rules, an outbound proxy) beyond "no ambient
  credentials + resource caps" (decision 4) is an operational policy left to implementation.
- **Notebook image contents and package policy** remain as deferred in ADR-0011 — shared across
  both spawners.

## References

- ADR-0011 — the embedded-notebooks shape and isolation properties this ADR makes spawner-portable;
  its decision 1 abstracts the spawner, and this ADR names the two realizations.
- ADR-0014 — SSO; written against "the spawner" generically and unchanged by this ADR.
- ADR-0015 — capability minting and the SQL proxy; spawner-independent and unchanged.
- ADR-0009 — the Kubernetes packaging and the single-user-pod egress NetworkPolicy that decision 4
  names as the strongest containment.
- ADR-0001 — the self-hosted (Compose) vs managed (Kubernetes) deployment framing this serves.
- `docs/architecture/notebooks.md` — the design overview, updated for the dual-spawner deployment.
