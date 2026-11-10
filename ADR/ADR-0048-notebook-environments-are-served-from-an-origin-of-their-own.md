<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0048 — Notebook environments are served from an origin of their own

- **Status:** Accepted
- **Date:** 2026-11-10
- **Parent:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (dec 2:
  the kernel is untrusted)
- **Related:** [ADR-0014](ADR-0014-notebook-hub-single-sign-on-from-the-platform-session.md) (dec 2, 6,
  7: sign-on, where the hub is served, chained sign-out), [ADR-0047](ADR-0047-one-browser-origin-for-the-app-and-every-plane-it-reaches.md)
  (dec 2: planes on the platform origin), [ADR-0004](ADR-0004-api-authentication-session-model-and-transport-security.md)
  (dec 2, 3, 6, 9: the session, its cookies, the origin allowlist), [ADR-0012](ADR-0012-per-session-credential-delivery-for-notebook-environments.md)
  (dec 1, 3: what a notebook may hold), [ADR-0015](ADR-0015-notebook-capability-minting-and-the-sql-access-proxy.md)
  (dec 1, 4: capabilities and demand-minting)

## Context

A kernel runs code the platform does not trust: the user's own, a package it installs, a notebook it
copies. Its output reaches the user's browser as active content, and the notebook interface runs it.
The kernel itself holds only scoped, revocable capabilities, and the session token never reaches it.

Script runs with the standing of the origin that serves it. While the hub and the environments are
served under a path of the platform's origin, kernel-produced script runs beside the app: the browser
attaches the session cookies to its platform requests and lets it read the CSRF token, so it acts with
the user's whole session, which a capability in the kernel was rejected for handing over.

A content policy or a sandbox on that origin cannot confine it: same-origin script can drive the app's
own pages. Self-hosted installs are often reached by an address or a local name with no subdomains.

## Decision

1. **Kernel-produced active content never runs on the platform origin.** The hub and every environment
   are served from a notebook origin that differs from the platform origin in host or port, and no
   path of the platform origin reaches them.
2. **The platform origin grants the notebook origin nothing.** It is in no cross-origin or socket
   allowlist (ADR-0004 dec 6, 9), so its script can neither read a platform response nor send a
   CSRF-checked request; platform cookies that reach it are never honoured there.
3. **The notebook origin is entered by a single-use ticket the platform demand-mints from the
   session.** It is a capability of its own audience (ADR-0015 dec 1, 4): it lives seconds, names one
   user, tenant and session, is spent by the exchange, and travels in a request body, never a URL; it
   is no data credential and cannot be exchanged for a platform session.
4. **The exchange opens a notebook session that is bound to the platform session and ends with it.**
   It is bound to the session, not to an access token, so refresh rotation leaves it; the proxy checks
   it on every request through the platform's one session verification, so every end of a platform
   session (ADR-0004 dec 2) ends it, and sign-out stays chained (ADR-0014 dec 7).
5. **Framing is explicit in both directions.** The hub and every environment permit framing by the
   platform origin only, and the platform frames the notebook origin only.
6. **The form of the notebook origin follows the deployment shape, and a hub without a distinct one
   does not start.** A deployment with names serves a sibling host; one reached by an address or a
   local name serves a second port on the same host and certificate.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The hub under a path of the platform origin | Kernel-produced script acts with the user's whole session |
| 1 | A content policy or sandbox on the platform origin | Same-origin script drives the app's own pages; a sandbox without the origin breaks the notebook interface |
| 1 | Accepting the cost while users run only their own code | A package or a copied notebook is code the user did not write, and the reach is the whole session |
| 2 | The notebook origin in the origin allowlist | Its script could read platform responses and send the CSRF header |
| 3 | The platform cookies scoped to the parent domain so the sibling host receives them | The session would reach the origin that runs untrusted content, and the sibling could overwrite the platform's cookies |
| 3 | A second login on the notebook origin | ADR-0011 dec 8: no second login |
| 3 | The ticket in the URL | URLs reach logs, history and referrers |
| 6 | A different registrable domain for notebooks | Its cookies are third-party inside the platform's frame, and browsers block them |
| 6 | A sibling host in every deployment | An install reached by address or local name has no sibling host |

## Consequences

**Positive**
- Script from a kernel cannot act with the user's platform session.
- Sign-on stays one session, verified once, with no second login.

**Negative**
- A second origin to route, certify and expose: a host name on the ingress or a port on the host.
- Entering notebooks is a ticket round trip, a moving part on the path to every environment.
- On a second port of the same host, the browser still sends the platform cookies there and lets
  script there overwrite them; only the refusals of decision 2 contain it.
- The app, the edge, the hub and its proxy all change, and an existing deployment exposes the new
  origin before upgrading.

## Deferred

| Open question | Settled by |
|---|---|

## References

- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): manual of decisions 1-6.
