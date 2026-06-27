<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0014: Notebook hub single sign-on from the platform session

- **ID:** ADR-0014
- **Status:** Proposed
- **Date:** 2026-06-27
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0011 (embedded notebooks), ADR-0012 (per-session credential delivery), ADR-0004 (API authentication, sessions and transport), ADR-0002 (verified-identity edge and anti-spoofing)

## Context and problem

ADR-0011 decided that notebooks are reached through the existing front door, that a logged-in user is not asked to log in again, and that no new public authentication surface is introduced (dec 8). Phase 2 builds the notebook hub that spawns per-user environments, and the first thing it needs is an answer to "who is this user, and what may they spawn." A hub of the kind ADR-0011 chose ships with its own authentication abstraction and, by default, its own login and user store — exactly the second login and second credential store that decision forbids. This ADR decides how the hub instead authenticates a user from the platform session that already exists, and how that identity becomes the tenant and profile of the environment it spawns.

The platform already authenticates every other request the same way: a short-lived JWT carried in an httpOnly session cookie, verified by the gateway, carrying the user's identity, tenant (`company_id`), and role (ADR-0004). There is exactly one implementation of that verification, and ADR-0000 is emphatic that it should stay exactly one — a decision re-expressed in a second place drifts and carries its bugs with it. So the hub must not grow its own copy of session-token verification; it must consume the result of the verification the platform already performs.

The platform also already has a pattern for letting a service trust an identity it did not verify itself. The ingestion edge verifies a hard credential and forwards a *verified identity* to an internal service that is reachable only through that edge, and the edge overwrites any identity headers a client tried to smuggle (ADR-0002). The hub is the same situation — an untrusted-facing surface that needs a trustworthy identity without re-doing the verification — and should reuse that pattern rather than invent a new one. The alternative, giving the hub the session-verification key so it can validate the cookie itself, both duplicates the verification ADR-0000 wants kept singular and widens where that key lives.

There is one thing this ADR is *not*. Authenticating the user to the hub ("who are you") is distinct from minting the credentials the kernel later uses to reach data ("what may this kernel touch"), which ADR-0012 already decided are minted per session by the spawner. This ADR decides the first; it hands the spawner a verified identity to mint against. It does not redesign the platform session (ADR-0004) nor the capability model (ADR-0012).

## Decision drivers

- **No second login and no second credential store (ADR-0011 dec 8).** The user is already authenticated to the platform; the hub must derive identity from that session, not establish its own.
- **Session verification stays singular (ADR-0000).** The hub must not reimplement JWT/session verification; there is one verification and the hub consumes its result rather than copying its logic or holding its key.
- **The platform already has a verified-identity-forwarding pattern (ADR-0002).** A trusted upstream verifies and forwards an identity to a network-isolated downstream that trusts it only because nothing else can reach it. The hub is the same shape and should reuse it.
- **The identity must carry tenant and role.** Spawning the right environment requires `company_id` (which tenant the environment is bound to) and the role (which profile — read-only analytics vs authoring, ADR-0011 dec 5); both already live in the session identity (ADR-0004).
- **Authentication to the hub is not authorization of the kernel.** This decision establishes identity at entry; the kernel's data credentials are minted separately by the spawner (ADR-0012). Keeping them separate avoids turning the entry credential into a data credential.

## Decision

1. **The hub authenticates users from the existing platform session; it has no login of its own and no user store of its own.** A user who holds a valid platform session (ADR-0004) is, by that fact, authenticated to the hub. The hub neither prompts for credentials nor maintains its own accounts; its notion of a user is the platform's.

2. **A trusted reverse proxy validates the session and forwards a verified identity to the hub; the hub trusts that identity and does not re-verify the session token.** Hub traffic passes through a trusted proxy at the existing edge that validates the platform session by deferring to the platform's single existing session-verification (ADR-0004) — not a second copy of it — and forwards the resulting verified identity to the hub. The hub runs in the mode where it trusts an upstream-asserted identity, mirroring the ingestion edge (ADR-0002).

3. **The hub is reachable only through that proxy, and the proxy overwrites any client-supplied identity.** The forwarded identity is trustworthy only because nothing but the proxy can present it: the hub and the per-user environments accept hub traffic solely from the trusted proxy (enforced as a network boundary, ADR-0009), and the proxy strips and replaces any identity headers a client attempted to send, exactly as the ingestion edge does (ADR-0002 dec 3). An identity that did not come through the proxy is never honoured.

4. **The verified identity carries the user, the tenant, and the role; the hub maps role to spawn profile and binds the environment to the tenant.** From the forwarded identity the hub takes the user (the hub identity), the `company_id` (the tenant the environment is bound to), and the role, which selects the profile — read-only analytics or authoring (ADR-0011 dec 5). The `company_id` carried here is the tenant the spawner mints capabilities against (ADR-0012) and that scopes the environment's isolation.

5. **The entry credential is not a data credential.** Authenticating to the hub conveys identity only; it grants the environment no access to data by itself. What the spawned kernel may reach is minted separately, per session, by the spawner (ADR-0012). The session token is never handed to the kernel as its data credential.

6. **No new public authentication surface is introduced.** The hub authenticates behind the same edge and origin as the rest of the product (ADR-0011 dec 8); there is no separately exposed hub login endpoint to attack, and the proxy is the existing trusted entry, not a new one.

## Alternatives considered

**A. The hub verifies the session cookie itself (a custom token authenticator holding the verification key).** *Rejected:* this duplicates the platform's session-token verification into the hub — a second place to keep audience, expiry, and signature checks correct, which ADR-0000 exists to prevent — and distributes the verification key to the hub. Consuming the result of the one existing verification (decision 2) achieves the same authentication without either cost.

**B. Make the platform an OpenID Connect provider and use the hub's OIDC authenticator.** *Rejected for now:* OIDC is the clean integration when an external identity provider is in play, but the platform authenticates with its own session JWT (ADR-0004), not as an OIDC provider, and standing up a conformant provider is a large effort to solve a problem the verified-identity-forwarding pattern (decision 2) already solves with components the platform has. It is the natural revisit if an external IdP is later adopted.

**C. Give the hub its own login and user store.** *Rejected:* a second login and a second set of accounts is precisely what ADR-0011 dec 8 forbids — it duplicates identity, asks an already-authenticated user to authenticate again, and creates an account store to keep in sync with the platform's.

**D. Have the hub trust a client-supplied identity header without restricting who can send it.** *Rejected:* an identity header that any caller can set is trivially forged — the header-smuggling risk ADR-0002 dec 3 calls out. The identity is trustworthy only when the hub is reachable only through the proxy and the proxy overwrites client-supplied identity (decision 3); without that, header-based identity is an open door.

## Consequences

### Positive

- A logged-in user reaches notebooks without a second login, and the hub keeps no accounts of its own (decisions 1, 6) — ADR-0011 dec 8 is satisfied rather than worked around.
- Session verification stays singular: the hub consumes the platform's one verification instead of copying it or holding its key (decision 2), so there is no second token-validation to drift (ADR-0000).
- The design reuses the platform's existing verified-identity-forwarding and anti-spoofing pattern (ADR-0002) rather than inventing a new trust mechanism for a high-stakes surface (decisions 2, 3).
- Tenant and profile fall out of the identity the platform already issues (decision 4), so spawning the correct, tenant-bound environment needs no new identity source.
- Entry identity and kernel data credentials stay separate (decision 5), so authenticating to the hub never, by itself, grants data access — the capability model (ADR-0012) remains the only grant of reach.

### Negative

- The hub's trust now rests on the proxy boundary being correct: if the hub becomes reachable other than through the proxy, or the proxy fails to overwrite client-supplied identity, forged identity becomes possible (decision 3). This is the same residual risk as the ingestion edge and must be enforced and tested as such.
- A trusted proxy step sits in front of the hub on every request, adding a hop and an availability dependency (decision 2).
- The platform session is short-lived and refreshed (ADR-0004) while a notebook session is long-running, so the relationship between session expiry, hub session lifetime, and logout propagation must be worked out rather than assumed (see Deferred decisions).
- Reusing the platform session for hub entry ties the hub to ADR-0004's session model; a future move to an external IdP (alternative B) would be a real change, not a configuration toggle.

## Deferred decisions

- **The verification handoff contract.** Whether the proxy authenticates hub requests via a subrequest to an existing platform verification endpoint or another trusted mechanism, and the exact shape of the forwarded identity (a set of headers versus a signed short-lived hub-audience assertion for integrity independent of the network boundary), is an implementation decision left to its own resolution.
- **Hub session lifetime, re-validation, and logout propagation.** How long a hub session lives relative to the platform session, whether and how often it re-checks the platform session, and how a platform logout tears down hub sessions and running environments are deferred.
- **Role-to-profile mapping configuration.** The concrete mapping from role to spawn profile (ADR-0011 dec 5) is owned by deployment configuration, not this ADR.
- **CSRF and same-origin embedding specifics.** How the hub entry interacts with the platform's CSRF protection (ADR-0004) and the iframe embedding (ADR-0011) is an implementation concern.
- **Per-session capability minting.** What the spawner mints once identity is established is decided in ADR-0012, not here.

## References

- ADR-0011 — embedded notebooks; dec 8 (no second login, no new public auth surface) and dec 5 (role-driven profiles) this ADR implements for authentication.
- ADR-0004 — the platform session, its claims, and the single verification this ADR consumes rather than duplicates.
- ADR-0002 — the verified-identity edge and header anti-spoofing pattern this ADR reuses for the hub.
- ADR-0012 — per-session credential delivery; the capability minting that follows authentication and that this ADR feeds a verified identity.
- ADR-0009 — the deployment and network-policy posture that enforces "reachable only through the proxy."
- M. Nygard, "Documenting Architecture Decisions" (2011); MADR — the decision-record format in use (per ADR-0000).
