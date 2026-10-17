<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0014 — Notebook hub single sign-on from the platform session

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (no second login, dec 8; role profiles, dec 5), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (the session and its one verification), [ADR-0002](ADR-0002-ingestion-authentication-and-device-identity.md) (the verified-identity forwarding pattern), [ADR-0012](ADR-0012-notebook-credential-delivery.md) and [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (what is minted after sign-on)

## Context

A notebook hub ships with its own login and user store, which ADR-0011 dec 8 forbids. The platform
session carries user, tenant and role and has one verification; a second copy would drift. The ingestion
edge already forwards a verified identity that its service trusts only from that edge (ADR-0002).

## Decision

1. **The hub authenticates users from the platform session and has no login or user store of its
   own.** A user with a valid platform session is, by that fact, authenticated to the hub.
2. **A trusted proxy validates the session and forwards a verified identity; the hub does not re-verify
   the session token.** On every request the proxy asks the platform's one session verification and
   forwards the verified user, tenant and role.
3. **The hub accepts a forwarded identity only with a proxy credential it verifies, and the proxy
   overwrites any client-supplied identity.** The credential is a shared secret set only by the proxy,
   only on hub traffic, and never held by an environment; every environment reaches the hub, so
   reachability proves nothing. The network boundary is defence in depth, and a hub without the
   credential does not start.
4. **Role selects the spawn profile and the environment is bound to the forwarded tenant.** That tenant
   is the one capabilities are minted against. The role-to-profile mapping is deployment configuration
   naming platform roles only; a role the platform does not have stops the hub rather than mapping.
5. **The entry credential is not a data credential.** Hub sign-on grants no data access; the session
   token never reaches the kernel.
6. **No new public authentication surface is introduced.** The hub is mounted under a path of the
   platform's own origin behind the existing edge, and it and every environment permit framing by that
   origin only.
7. **The hub re-checks the forwarded identity, and sign-out is chained.** At a bounded age and before
   every spawn, a changed tenant or role stops the user's environments and a different or invalid user
   ends the hub login; signing out of the platform also signs out of the hub, which stops the
   environments and so revokes their capabilities.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The platform as an OpenID Connect provider | A large effort for a problem forwarding already solves; the revisit if an external identity provider is adopted |
| 1 | The hub's own login and user store | A second login and a second account store to keep in sync |
| 2 | The hub verifies the session cookie itself | A second verification to keep correct, and the verification key distributed to the hub |
| 3 | A client-supplied identity trusted without restricting who sends it | Trivially forged |
| 3 | Trusting the forwarded identity by the hub's network boundary alone | Every environment reaches the same hub, so any of them could forge a login |
| 7 | Hub session lifetime tied only to platform session expiry | A role change or sign-out would take effect only when a timer ran out |

## Consequences

**Positive**
- A logged-in user reaches notebooks without a second login; session verification stays singular.

**Negative**
- The hub's trust rests on one shared secret (decision 3): if it reached an environment, that environment could forge a login.
- The proxy is a hop and an availability dependency on every hub request.
- Re-checking costs a periodic check on hub traffic, and a role or tenant change stops running work.
- Request-forgery protection for hub requests is the hub's own, not the platform API's.

## Deferred

| Open question | Settled by |
|---|---|
| Sign-on from an external identity provider (dec 1 alternatives) | First deployment that requires one |

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): request path.
- `services/notebook_hub/sso-nginx.conf`, `jupyterhub_config.py`; `services/frontend/src/services/session.js`.
