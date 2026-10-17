<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0018: Notebook hub spawner portability (compose and Kubernetes) (rev 2)

- **ID:** ADR-0018
- **Status:** Accepted
- **Date:** 2026-06-28 (rev 1: 2026-10-13; rev 2: 2026-10-17)
- **Project:** IndustryFlow
- **Parent:** ADR-0011 (embedded notebooks — the shape this refines)
- **Companions:** ADR-0009 (Kubernetes deployment and packaging), ADR-0014 (notebook hub SSO), ADR-0015 (capability minting + SQL proxy)
- **Related:** ADR-0001 (self-hosted vs managed deployment), ADR-0002 (network-boundary posture)
- **Supersedes:** ADR-0018 rev 0 (2026-06-28 — decision 2 restated ADR-0011 rev 0's "short-lived single-tenant read-only principal"; rev 1 restates it as ADR-0011 rev 1 and ADR-0015 rev 2 decide it); ADR-0018 rev 1 (2026-10-13 — placed the Docker-host environment on a dedicated internal network; rev 2 restates decision 4 for a dedicated network that keeps a route off the host)

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
**Docker Compose on a single host** (the single-host self-hosted shape ADR-0009 rev 1 supports);
Kubernetes is supported
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

**Revision 1 (2026-10-13).** Decision 2 enumerated ADR-0011's invariants by restating them, and so carried ADR-0011 rev 0's wording for direct SQL — a *short-lived* single-tenant principal — after that wording had been reversed: the principal is standing and per tenant, and the short-lived, revocable element is the capability the kernel presents (ADR-0011 rev 1). ADR-0015 rev 2 has since moved the SQL path's tenant boundary onto the identity the proxy logs in as. Rev 1 restates decision 2 to match both. Nothing about spawner portability changes: the restated invariants are as spawner-independent as the old ones.

**Revision 2 (2026-10-17).** Decision 4 placed the Docker-host environment on a *dedicated internal network*. The network the compose profile built is dedicated but not internal: its membership is restricted to the hub, the trusted mediators and the object store, while it keeps a route off the host, because an address a mediator hands a kernel may name the platform's public edge rather than an interior host (ADR-0030). Rev 2 restates decision 4 to say what the network bounds and what it does not. The deferred implementation of both profiles has since been built; that outcome is recorded under Deferred decisions.

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
   the kernel (dec 2); tenant isolation by database privilege via a per-tenant read-only principal
   (dec 3, ADR-0012); data reached only through the tenant-scoped API and, for authoring, through
   the SQL proxy, which runs the session as that tenant's standing read-only identity while the
   kernel presents only a short-lived, revocable handle (dec 4/6 as revised by ADR-0011 rev 1,
   ADR-0015 rev 2) *(rev 1)*; SSO from
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

   > **rev 2 (2026-10-17): on a Docker host the environment runs on a dedicated network whose membership is restricted, not on a network without a route off the host.** The network carries only the hub, the trusted mediators a kernel is meant to use, and the object store those mediators answer with pre-signed addresses. The capability store, the tracking server, the database and the stream processors, among others, are not on it, so a kernel cannot address them. The network keeps its route off the host, so outbound egress to destinations outside the platform is bounded by the host alone. The rest of decision 4 stands: the compose profile's primary boundary is the isolation of decisions 2–3, and allowlist-grade egress containment remains a Kubernetes property.

5. **The standalone root Jupyter is retired by the hub, not hardened into permanence.** The shared,
   root, authentication-disabled, shared-credential Jupyter container (ADR-0011 alternative A) is
   replaced — on Compose by the DockerSpawner hub, on Kubernetes by the KubeSpawner hub. Should a
   purely local developer notebook be kept in the interim, it must be non-root, authenticated, and
   bound to loopback; it remains an out-of-band developer tool with no per-user identity and no
   multi-tenant path, exactly as ADR-0011 alternative A allows, and it is never a product surface.

## Alternatives considered

**A. Keep KubeSpawner only; require a cluster for any multi-tenant notebooks.** *Rejected:* it
leaves the platform's actual live deployment (single-host Compose, ADR-0009 rev 1) with no conforming notebook
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
  *Resolved since acceptance.* Both profiles are built from one hub configuration that selects the
  spawner. The compose profile runs on the single-host deployment, and its kernel network is checked
  by the compose smoke gate (ADR-0026). The Kubernetes profile is rendered and linted in CI but has
  not been run on a cluster.
- **Compose egress hardening.** Whether and how far the compose profile tightens egress (a
  dedicated internal network, host firewall rules, an outbound proxy) beyond "no ambient
  credentials + resource caps" (decision 4) is an operational policy left to implementation.
  *(rev 2)* The dedicated network with restricted membership is built (decision 4 as revised); a
  network without a route off the host, host firewall rules and an outbound proxy are not, and
  remain open.
- **Notebook image contents and package policy** remain as deferred in ADR-0011 — shared across
  both spawners.

## References

- ADR-0011 — the embedded-notebooks shape and isolation properties this ADR makes spawner-portable;
  its decision 1 abstracts the spawner, and this ADR names the two realizations.
- ADR-0014 — SSO; written against "the spawner" generically and unchanged by this ADR.
- ADR-0015 — capability minting and the SQL proxy; spawner-independent and unchanged.
- ADR-0009 (rev 1) — the Kubernetes packaging and the single-user-pod egress NetworkPolicy that
  decision 4 names as the strongest containment; rev 1 also establishes single-host Compose as a
  supported self-hosted deployment shape, which is the premise the Compose spawner here relies on.
- ADR-0001 — the self-hosted vs commercial-managed *business/licensing* split this serves. (The
  mapping of self-hosted→Compose and managed→Kubernetes is ADR-0009's deployment decision, not
  ADR-0001's; ADR-0001 only requires that both coexist under one architecture.)
- `docs/architecture/notebooks.md` — the design overview, updated for the dual-spawner deployment.
