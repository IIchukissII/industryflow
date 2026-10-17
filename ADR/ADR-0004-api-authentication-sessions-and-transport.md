<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0004 — API authentication, session model and transport security

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-17
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md)
- **Related:** [ADR-0002](ADR-0002-ingestion-authentication-and-device-identity.md) (shared edge),
  [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (tenant claim),
  [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (deployment shapes),
  [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (capabilities),
  [ADR-0017](ADR-0017-database-tls-and-authentication.md) (shared internal CA)

## Context

The HTTP API serves humans and services, a trust domain distinct from device ingestion. A long-lived
stateless token cannot be cancelled once leaked; a credential readable by page script is exfiltrated
by injected script; a cookie is sent automatically, which exposes cross-site request forgery.
Self-hosted deployments serve internal hostnames no public CA issues for.

## Decision

1. **All external traffic, live sockets included, is TLS-protected; plaintext is redirected.** TLS
   terminates at the edge shared with ingestion; plaintext is acceptable only for throwaway local
   development.
2. **A session is a short-lived JWT access token carrying the tenant claim, plus a rotating,
   server-side refresh credential.** Each refresh claims the credential atomically and issues a
   successor, so reuse is refused. Logout,
   compromise, a password change or reset, and deactivating or deleting the account or its company
   end all the user's sessions and the notebook capabilities minted from them, and every request
   re-reads the account.
3. **The browser holds tokens in httpOnly, Secure, SameSite cookies set by the backend.** Every
   state-changing request authenticated by the session cookie must echo a double-submit CSRF token in
   a header; bearer-authenticated requests and the authentication endpoints are exempt.
4. **Every data endpoint requires authentication.** Only probes that expose no tenant data are exempt.
5. **Authorization is scoped to the caller's tenant.** Cross-company access requires a distinct,
   explicitly modelled superuser.
6. **CORS uses an explicit origin allowlist and never combines a wildcard with credentials.**
7. **Secrets are never logged.**
8. **The browser-facing certificate's issuer follows the deployment shape (ADR-0009).** Managed uses
   an auto-renewed public ACME certificate; self-hosted uses an internal CA whose root is installed on
   clients once and also signs the database certificate (ADR-0017).
9. **A live socket is held to the same session as any request.** Its handshake authenticates by the
   session cookie only and is origin-checked against decision 6's allowlist; it closes when its
   credential expires, or at a periodic re-check once its session has ended or its account is
   inactive.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Stateless JWT, long- or short-lived, without refresh | Not revocable; short lifetimes force re-login |
| Tokens in browser local storage with a CSP | Still readable by injected script |
| TLS terminated in each service | Certificate handling in every replica |
| Public ACME in every deployment | No public CA issues for an internal hostname |
| Separate internal CAs for edge and database | Two roots to install and rotate |
| End sessions only on logout | A refreshing session survives the password change made after a leak |
| Token-in-URL fallback for sockets | A credential channel readable by script and logged |

## Consequences

**Positive**
- Tokens and readings are encrypted in transit, and injected script cannot read the session.
- A leaked access token lapses within minutes; a session, its capabilities and its sockets end at
  once on revocation or account change.
- No caller reaches another tenant's data or any data unauthenticated.

**Negative**
- The platform runs a session store and every state-changing browser request carries a CSRF token.
- Each deployment shape needs its certificate machinery; local development needs TLS or an explicit
  HTTP exception.
- An access token is not revoked individually; a password change logs out every device.

## Deferred

| Open question | Settled by |
|---|---|
| Internal service-to-service authentication | An ADR, when an internal call carries tenant authority beyond the interim mechanism |
| E-mail delivery of reset and verification credentials | The first deployment that enables self-service reset |

## References

- [docs/operations/tls.md](../docs/operations/tls.md): the internal CA and installing its root.
- `services/api_gateway/routers/auth_tokens.py`, `routers/websocket.py`: sessions and sockets.
