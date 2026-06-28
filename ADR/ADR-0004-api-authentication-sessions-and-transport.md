<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0004: API authentication, session model, and transport security (rev 1)

- **ID:** ADR-0004
- **Status:** Accepted
- **Date:** 2026-06-28
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0002 (ingestion authentication & device identity), ADR-0003 (tenant→schema resolution)
- **Related:** ADR-0009 (rev 1 — the managed/self-hosted deployment shapes that decision 8 keys the edge certificate to), ADR-0017 (the self-hosted internal CA whose root the database cert shares)
- **Supersedes:** ADR-0004 rev 0 (2026-06-26 — left "server-certificate issuance" deferred and stated decision 1 as if the edge certificate were always a public ACME cert; rev 1 resolves that deferred item as decision 8: public ACME for the managed edge, an internal CA for self-hosting)

## Context and problem

The HTTP API is the human/service trust domain (the frontend, operators, service-to-service calls), as distinct from the device-ingestion domain decided in ADR-0002. Its current security posture is weak across every layer of the request, and the review found concrete defects at each:

- **Transport is plaintext.** The frontend talks to the API over `http://` and to the websocket over `ws://` (hardcoded URLs), so access tokens and sensor data cross the network unencrypted. HTTP was a quick solution to get the system running, never an intended end state.
- **Sessions are long-lived and unrevocable.** Authentication issues a stateless JWT with a seven-day lifetime and no refresh or revocation path (`services/api_gateway/users.py:63`). A leaked token is valid for a week and cannot be cancelled.
- **Tokens sit where XSS can read them.** The frontend stores the JWT in `localStorage`, readable by any injected script.
- **Some data endpoints require no authentication.** `GET /api/users` returns every user of every tenant with no auth dependency (`main.py:139`), a cross-tenant data leak.
- **Authorization is not scoped to the tenant.** `require_role("admin")` checks only the role string, so a tenant-A admin can read, edit, and delete tenant-B records (`routers/companies.py`).
- **CORS is misconfigured.** `allow_origins=["*"]` is combined with `allow_credentials=True`, which is both browser-rejected and a credential-CSRF risk.
- **Secrets are logged.** Password-reset and verification tokens are `print`ed (`users.py:42,48`).

These are not seven unrelated bugs; they are the consequence of never deciding how the API authenticates a caller, how a session lives and dies, how the browser holds the credential, and how the traffic is protected in transit. This ADR makes those decisions for the API trust domain. It is a companion to ADR-0002 (which owns the device domain and introduces the TLS-terminating reverse proxy this ADR reuses) and to ADR-0003 (which relies on the access token carrying the tenant claim).

**Revision 1 (2026-06-28).** Rev 0 stated decision 1 as if the browser-facing certificate were always a publicly-trusted ACME cert, and listed "server-certificate issuance" as a deferred decision. That left a real gap: a **self-hosted single-host deployment** (ADR-0009 rev 1) serves an internal hostname such as `industryflow.local`, for which no public CA will issue a certificate — so in practice self-hosting uses an **internal** CA (`scripts/gen-internal-ca.sh` on Compose; cert-manager on Kubernetes), and ADR-0017 already reuses that same internal root for the database cert. The deferred item and the ACME-only wording therefore drifted from how the platform actually runs, and the "self-hosted edge uses an internal CA" decision had no single home — it was being asserted inside ADR-0017's context and the glossary rather than owned here. Rev 1 resolves the deferred item as **decision 8**, keying the edge certificate's issuer to the deployment shape, and tightens decision 1 to defer to it.

## Decision drivers

- **Credentials and data must be encrypted in transit.** Plaintext HTTP exposes every token and every reading on the wire; encryption is a baseline, not a feature.
- **A session must be short and revocable.** A leaked or stale credential must expire quickly and be cancellable on logout or compromise, which a long-lived stateless JWT is not.
- **A browser-held credential must resist theft by injected script.** Storage the page's JavaScript can read is exfiltrated by any XSS; the credential should be unreadable to script.
- **Every data path must be authenticated and tenant-scoped.** No endpoint may return data without authentication, and no authenticated caller may reach another tenant's data.
- **CORS must not trade away credentials.** An explicit origin allowlist, never `*` with credentials.
- **TLS termination belongs at the edge proxy.** Consistent with ADR-0002, certificate handling lives at the reverse proxy, not in each service.

## Decision

### Transport

1. **All external traffic is served over HTTPS, and websockets over `wss://`; plaintext HTTP is redirected to HTTPS.** TLS is terminated at the reverse proxy (the same edge component ADR-0002 uses for ingestion). HTTP-only operation is acceptable only for throwaway local development, never for any shared or deployed environment. The browser-facing server certificate is a **distinct** PKI concern from ADR-0002's device-mTLS CA — the device CA authenticates clients to us; the server certificate authenticates us to browsers. Which authority issues that server certificate depends on the deployment shape, decided in decision 8.

### Session model

2. **Authentication issues a short-lived access token plus a rotating refresh token, with server-side revocation.** The access token (a JWT, lifetime on the order of minutes) carries the tenant claim relied on by ADR-0003. A longer-lived refresh token is tracked server-side (in a session/denylist store) and rotated on each use; logout or a compromise revokes it immediately, ending the session. Stateless seven-day tokens with no revocation are retired.

### Browser credential storage

3. **The browser holds tokens in httpOnly, Secure, SameSite cookies set by the backend, not in `localStorage`.** Because the cookie is httpOnly, page JavaScript cannot read it, so an XSS cannot exfiltrate the session (the `localStorage` exposure is closed). The `Secure` attribute depends on decision 1's HTTPS. Because cookies are sent automatically, **CSRF protection is required** (a double-submit token and/or strict `SameSite`); the specific mechanism is deferred below.

### Authorization

4. **Every data endpoint requires authentication.** There are no unauthenticated data endpoints; the unauthenticated `GET /api/users` is removed or placed behind authentication and authorization. Health/readiness probes that expose no tenant data are the only exceptions.

5. **Authorization is scoped to the authenticated principal's tenant.** Every tenant-data query is constrained to the caller's `company_id`; a role such as `admin` grants privileges only within the caller's own company. Cross-company access requires a distinct, explicitly modelled superuser role, not the per-tenant admin role. This closes the cross-tenant admin defect.

### CORS and secret handling

6. **CORS uses an explicit origin allowlist and never combines `*` with credentials.** The allowed origins are configuration values living in `.env`/compose (per ADR-0000), not hardcoded; the policy — explicit list, credentials only with named origins — is fixed here.

7. **Secrets are never logged.** Tokens, password-reset and verification links, and credentials are never written to logs or stdout; the `print`ed reset/verification tokens are removed.

### Edge-certificate issuer *(rev 1)*

8. **The browser-facing certificate's issuing authority is keyed to the deployment shape (ADR-0009 rev 1).** The **managed** offering, served on a public hostname, uses a **publicly-trusted ACME / Let's Encrypt** certificate, auto-renewed. A **self-hosted** deployment, typically served on an internal hostname for which no public CA will issue (e.g. `industryflow.local`), uses an **internal CA**: `scripts/gen-internal-ca.sh` on single-host Compose, or a cert-manager issuer on Kubernetes (the in-cluster equivalent). The internal CA's `ca.crt` is installed as a trusted root on client devices once. This is the same internal root ADR-0017 reuses for the TimescaleDB server certificate, so a self-hosted operator runs **one internal CA for both the API edge and the database**. In all cases the edge certificate authenticates the *server* and remains distinct from ADR-0002's device-mTLS client CA (decision 1). A self-hoster with a public hostname *may* opt into ACME instead; the internal CA is the default because it works without one.

## Alternatives considered

**A. Keep the stateless long-lived JWT with no revocation.** *Rejected:* a leaked token is then valid for its full week and cannot be cancelled — the defect this ADR exists to fix.

**B. Shorten the JWT but stay stateless with no refresh or revocation.** *Rejected:* it still cannot revoke a compromised token before expiry, and short statelessness without refresh forces frequent full re-logins. The chosen access+refresh model gives short exposure *and* revocability.

**C. Keep tokens in `localStorage` (optionally with a strict CSP).** *Rejected as the primary control:* `localStorage` is readable by any injected script, so XSS exfiltrates the session; a CSP reduces XSS likelihood but httpOnly cookies remove the *readability* of the token entirely, which is the stronger guarantee. CSP remains worthwhile as defence in depth but is not a substitute.

**D. Terminate TLS inside each FastAPI service.** *Rejected:* it scatters certificate and renewal handling across every service and replica; ADR-0002 already established the edge proxy as the place TLS is terminated, and this ADR reuses it.

**E. Encrypt only the public edge and leave internal service-to-service traffic on plaintext HTTP.** *Accepted in part / deferred:* external traffic is HTTPS per decision 1; whether internal service-to-service calls additionally require mTLS or are trusted on an isolated network is the service-to-service-auth question ADR-0002 deferred, and is not decided here.

**F. Require a publicly-trusted ACME certificate for every deployment *(rev 1)*.** *Rejected:* a self-hosted single-host deployment serves an internal hostname (`industryflow.local`) that no public CA will issue a certificate for; mandating ACME would make self-hosting impossible without a public DNS name the operator may not have. Decision 8 keys the issuer to the deployment shape instead, with ACME as the managed default and the internal CA as the self-hosted default.

**G. Issue the self-hosted edge certificate from a fresh, edge-only internal CA, separate from the database CA *(rev 1)*.** *Rejected:* it would create two internal roots a self-hoster must install and rotate where one suffices. ADR-0017 already issues the DB cert from `gen-internal-ca.sh`; reusing that single internal root for the edge means clients trust one CA for both surfaces, which is simpler to operate without weakening isolation (both are server certs in the same self-hosted trust domain).

## Consequences

### Positive

- Tokens and sensor data are encrypted in transit; the plaintext-HTTP exposure is closed.
- Sessions are short and revocable: a leaked access token expires in minutes and a compromised session can be cancelled server-side, instead of being valid and unrevocable for a week.
- An XSS can no longer read the session token, because httpOnly cookies are invisible to script.
- No endpoint returns data without authentication, and no authenticated caller can reach another tenant's data; the cross-tenant leak and the cross-tenant admin defects are closed.
- CORS no longer reflects arbitrary origins with credentials, and secrets no longer leak to logs.

### Negative

- Refresh tokens with rotation and revocation require a server-side session/denylist store (e.g. Redis or the database) and the rotation/invalidation logic to operate it — real state the stateless model did not have.
- httpOnly cookies introduce CSRF exposure that must be mitigated (a CSRF token and/or strict `SameSite`), and they change the frontend auth flow from reading a bearer token to relying on the cookie.
- HTTPS requires a server certificate and its renewal for the API/frontend edge — operational machinery distinct from the device CA. Per decision 8 this is ACME auto-renewal (managed) or an internal-CA root that self-hosters install on clients once (self-hosted); local development still needs a TLS story (e.g. mkcert / the internal CA) or an explicit, clearly-scoped HTTP dev exception.
- Tightening every endpoint to be authenticated and tenant-scoped is a cross-service change with regression risk that must be done deliberately.

## Deferred decisions

- **CSRF mechanism.** The specific anti-CSRF approach (double-submit cookie, synchroniser token, `SameSite` strictness level) is left to implementation.
- **Refresh-token store and lifetimes.** The store backing revocation, the access/refresh lifetimes, and rotation/reuse-detection details are unspecified here.
- ~~**Server-certificate issuance.**~~ *Resolved in rev 1, decision 8:* public ACME for the managed edge, an internal CA (`gen-internal-ca.sh` / cert-manager) for self-hosting. Renewal automation remains an infrastructure concern (ACME auto-renewal; internal-CA leaf reissue per ADR-0017).
- **Internal service-to-service authentication.** Whether internal calls use mTLS, signed tokens, or network trust is the deferred ADR-0002 question, not resolved here.
- **Password-reset delivery.** Replacing the logged reset/verification tokens with an email delivery path is an implementation concern.

## References

- IndustryFlow review (2026-06-26): plaintext HTTP/ws, 7-day unrevocable JWT, localStorage token, unauthenticated `/api/users`, unscoped admin role, `*`+credentials CORS, logged reset tokens — internal report.
- ADR-0002 — ingestion authentication & device identity; owns the TLS-terminating edge proxy reused here and the device-mTLS CA distinct from the server certificate.
- ADR-0003 — tenant→schema resolution; consumes the tenant claim carried by the access token decided here.
- ADR-0009 (rev 1) — the managed/self-hosted deployment shapes decision 8 keys the edge certificate to.
- ADR-0017 — database TLS; reuses the self-hosted internal CA (`gen-internal-ca.sh`) decision 8 establishes for the edge.
