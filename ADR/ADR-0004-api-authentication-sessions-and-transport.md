<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0004 — API authentication, session model and transport security

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-11-01
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
   successor; the claimed credential presented again within a short grace (a concurrent refresh) is
   answered with that successor, and later ends its session. Logout ends its session and the
   user's notebook capabilities; compromise, a password change or reset, a move to another company,
   and deactivating or deleting the account or its company end all the user's sessions and those
   capabilities. Every request re-reads the account and refuses an access token whose session has
   ended, and is refused while that cannot be read.
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
10. **Password guessing is slowed per account and source, never locked out.** Consecutive failures
    for one account from one source wait exponentially longer, up to a cap; each attempt is counted
    before its password is checked, and one inside the wait is refused unchecked. Where the edge sees
    the clients' own addresses it also limits each source across accounts. No account is locked, as
    no channel exists to unlock one, and one source's failures never slow the owner at another.
11. **A platform with no superuser gets one at start, held to a password change while its password is
    the published default.** It is created only while no superuser exists, in the oldest tenant or a
    first one; until its holder sets their own password it may read itself, change that password and
    sign out, and nothing more. A deployment that names its own first password is not held.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | TLS terminated in each service | Certificate and renewal handling scattered across every service and replica |
| 2 | A long-lived stateless JWT with no revocation | A leaked token stays valid for its whole lifetime and cannot be cancelled |
| 2 | A short-lived stateless JWT without refresh or revocation | Still not revocable before expiry, and short lifetimes force repeated full logins |
| 2 | Ending sessions only on logout and compromise | A refreshing session survives the password change made after a leak, and its capabilities keep working |
| 2 | Refusing a reused rotated credential and keeping its session | Reuse after the rotation means two holders, and the stolen copy's session would live on |
| 2 | Ending the session on every second presentation | Two tabs of one browser refreshing at once would sign the user out |
| 2 | Logout ending all the user's sessions | Signing out on one device would sign out every other |
| 2 | Checking for an ended session only on live sockets | An access token keeps working after its logout or a password change until it expires |
| 2 | Admitting a request when the record of ended sessions cannot be read | While it is unreadable no ended session is enforced, and a leaked token is good for its whole lifetime |
| 2 | Comparing a token's issue time with an end exactly | A replica whose clock runs ahead issues tokens that outlive the end for their whole lifetime |
| 2 | Issuing a token within the allowance after an end | It counts as issued before the end and is refused for its whole lifetime |
| 2 | Keeping the sessions of a user moved to another company | Their tokens name the old tenant, whose data stays reachable through them until they expire |
| 3 | Tokens in browser local storage, with a CSP | Injected script can still read the token; a CSP lowers the odds but not the readability |
| 6 | Wildcard origins combined with credentials | Browsers reject it, and it invites cross-site use of the credential |
| 8 | A public ACME certificate in every deployment | No public CA issues for an internal hostname, so self-hosting would need a public name |
| 8 | A separate internal CA for the edge and for the database | Two roots to install and rotate where one suffices |
| 9 | A credential carried in the socket URL, as a fallback to the cookie | A second credential channel, readable by script and written to logs |
| 9 | A socket handshake accepted from any origin | The automatically sent cookie would let a foreign page open the socket |
| 9 | A socket that lives as long as its connection | It outlives the session and account behind it |
| 10 | No limit on password sign-in | An online guess costs nothing, so a weak password falls to a script |
| 10 | Locking an account after repeated failures | No e-mail or other channel exists to unlock it, so anyone could lock out any user |
| 10 | A limit per source address only | Many addresses guess one account unhindered |
| 10 | A limit per account only, across sources | One wrong password per wait from anywhere keeps the owner refused for ever |
| 10 | A per-account limit across sources on top | Whoever holds it at its cap locks the owner out the same way |
| 10 | Checking the wait and counting the failure as separate steps | Guesses sent at the same moment all pass the check |
| 10 | Counting failures only for existing accounts | The difference in answers reveals which addresses have accounts |
| 11 | Creating the first superuser only by a command run inside the platform | A first sign-in needs shell access and a tenant made by hand first |
| 11 | A published default password with a change the holder may skip | The well-known password stays valid against cross-tenant authority |
| 11 | A generated first password written to the log or the console | A secret in the log stays readable wherever the log goes |
| 11 | Creating the account again whenever it is missing | A removed account would come back with the default password beside other superusers |
| 11 | A superuser outside every tenant | Every tenant-scoped page and claim assumes a tenant, which this one would lack |

## Consequences

**Positive**
- Tokens and readings are encrypted in transit, and injected script cannot read the session.
- A session's access tokens, capabilities and sockets stop working at once when it ends; a leaked
  access token whose session does not end lapses within minutes.
- A stolen refresh credential replayed after its owner refreshed ends the session for both.
- Online password guessing falls to a handful of attempts an hour per account from each source.
- A fresh platform is usable from the browser at once, with no command run inside it.
- No caller reaches another tenant's data or any data unauthenticated.

**Negative**
- The platform runs a session store and every state-changing browser request carries a CSRF token.
- Each deployment shape needs its certificate machinery; local development needs TLS or an explicit
  HTTP exception.
- An access token is not revoked individually; a password change logs out every device.
- Every authenticated request reads the session store, and none is served while it is unreachable.
- An attacker with many source addresses gets that handful from each of them; an IPv6 network
  allotted to one host counts as one source.
- A refresh whose answer is lost, retried after the grace, ends its own session.
- Until its holder first signs in, a platform started with the defaults answers to a published password.
- A stolen refresh credential used within the grace after a rotation is not detected.
- A sign-in or refresh within a clock-skew allowance after an end of all the user's sessions waits
  the allowance out, since a token issued in it would count as issued before the end.

## Deferred

| Open question | Settled by |
|---|---|
| Internal service-to-service authentication | An ADR, when an internal call carries tenant authority beyond the interim mechanism |
| E-mail delivery of reset and verification credentials | The first deployment that enables self-service reset |
| Slowing password guessing behind a proxy that hides client addresses (neither limit applies there) | The first deployment behind a Kubernetes Ingress |

## References

- [docs/operations/tls.md](../docs/operations/tls.md): the internal CA and installing its root.
- `services/api_gateway/routers/auth_tokens.py`, `routers/websocket.py`: sessions and sockets.
- `libs/industryflow_contracts/industryflow_contracts/sessions.py`: the record of ended sessions every
  verifier reads.
