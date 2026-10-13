<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Authentication

IndustryFlow has two authentication domains:

- **Human / service API access** — JWT, carried for browsers in **httpOnly cookies** with
  CSRF protection, and for API/service clients as a bearer token (this document,
  **[ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md)**).
- **Device / gateway ingestion** — **mutual TLS** with client certificates from the
  IndustryFlow device CA (**[ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md)**
  / **[ADR-0007](../../ADR/ADR-0007-public-key-infrastructure.md)**). See
  **[device-mtls.md](device-mtls.md)**.

All external traffic is HTTPS (websockets over `wss://`), terminated at the TLS edge — see
**[tls.md](tls.md)**.

## Browser sessions: httpOnly cookies + CSRF

The web client never holds a token in JavaScript. On `POST /auth/login` the gateway sets:

| Cookie | Contents | Flags | Path |
|--------|----------|-------|------|
| `if_access` | access JWT | httpOnly, Secure, SameSite=lax | `/` |
| `if_refresh` | refresh token | httpOnly, Secure, SameSite=lax | `/auth` |
| `if_csrf` | CSRF token (mirrors a value) | Secure, **JS-readable** | `/` |

The browser sends the cookies automatically; the SPA reads `if_csrf` and echoes it in an
`X-CSRF-Token` header on every unsafe (POST/PUT/PATCH/DELETE) request. The gateway enforces
**double-submit CSRF** (`X-CSRF-Token` must equal `if_csrf`) on cookie-authenticated unsafe
requests — bearer/API clients are exempt, and `/auth/*` is exempt.

The websockets (`/ws/sensors`, `/ws/sensors/{equipment_id}`) authenticate only from the same
`if_access` cookie on the `wss://` handshake. A `?token=` in the URL is not accepted, since URLs
end up in access logs. A socket lives no longer than its session: it closes with code **4401** when
the access token expires or the session it was opened in has ended — by a logout of that session, or
by anything that ends all of the user's sessions (the client refreshes via `/auth/refresh` and
reconnects, or signs in again) — and with **1008** when the user is inactive or deactivated or their
company is suspended. An open socket re-checks every `WS_SESSION_RECHECK_SECONDS` (default 60), so
it outlives the end of its session by at most that.
Because CORS does not apply to websockets and the browser sends the cookie on any handshake, the
gateway also checks the handshake's `Origin`: it must be in `CORS_ORIGINS` or be exactly the
host the request was addressed to, else the socket closes with 1008. List the frontend's public
origin, port included (e.g. `https://<host>:3443`), in `CORS_ORIGINS`.

`Secure` cookies require HTTPS, which every edge serves. `COOKIE_SECURE` defaults to `true`
(gateway, compose, `.env.example` and Helm); set it to `false` only for a throwaway plain-HTTP
development setup.

## Tokens

| Token | Form | Lifetime | Carried as |
|-------|------|----------|-----------|
| **Access** | JWT (HS256), signed by `JWT_SECRET_KEY` | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (default **30 min**) | `if_access` cookie (browser) **or** `Authorization: Bearer` (API/service) |
| **Refresh** | opaque random string in Redis | `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (default **7 days**) | `if_refresh` cookie, or the `/auth/refresh` body |

The access JWT carries **`company_id` and `role` claims** (`CompanyClaimJWTStrategy` in
`services/api_gateway/users.py`); downstream services read the tenant from the claim rather
than looking it up (**[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**).
Refresh tokens are **single-use and rotated** on every refresh (reuse is detected and
rejected) and are **revoked server-side** on logout, together with the user's notebook
capabilities (ADR-0012 dec 6), including any model-upload handle minted for them (indexed per user
by the ML service). They are indexed per user, and **all** of a user's sessions and
notebook capabilities are revoked when the password is changed or reset, when the user is
deactivated, when their company is deactivated (by `PUT /api/companies/{id}` with
`is_active: false` or by `DELETE`, which take the same path) and when the user is deleted. Each
refresh token also carries a fingerprint of the password hash it was issued under, so a password
set outside the gateway (the admin CLI) ends the existing sessions at their next refresh.

A refresh token carries the id of its session from rotation to rotation, and every access token
minted in the session names it (`sid`) along with its issue time (`iat`). Ending a session records
that it ended, and ending all of a user's sessions records when; both records live for one
access-token lifetime, which is as long as any token issued before them can. That is what an open
socket re-checks.

**Suspended companies.** A user whose company is inactive is refused at sign-in, on every
session-authenticated request, at refresh, on a socket and on a notebook capability. The gateway
reads the company's flag through a per-process cache held for `COMPANY_STATUS_CACHE_SECONDS`
(default 30); the replica that changes the flag sees it at once. Deactivating also writes a
suspension marker (`nbcap-suspended:<company_id>`) into the capability store, which the SQL proxy,
the tracking gateway, the cold-store broker, the hub and the model-upload minter all consult, so
no handle bound to the company resolves or is minted anywhere from that moment. Reactivating
removes the marker; it restores no session.

## Endpoints (API Gateway)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/auth/login` | Authenticate (form: `username`=email, `password`) → sets auth cookies; also returns the tokens for API clients |
| `POST` | `/auth/refresh` | Rotate the session (refresh from cookie or body) |
| `POST` | `/auth/logout` | Revoke the refresh token and clear the cookies (204) |
| `POST` | `/auth/jwt/login` | fastapi-users login — access token only (kept for compatibility) |
| `GET`  | `/users/me` | Current user |
| `PATCH` | `/users/me` | Change own email/password (tenant and role are not settable) |
| `POST`/`PATCH`/`DELETE` | `/api/users[/{id}]` | Admin user management, scoped to the admin's company ([user management](user-management.md)) |

```bash
# Browser-style: cookies + CSRF over HTTPS
curl -k -c jar.txt -X POST https://<host>/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=user@example.com&password=<password>'
CSRF=$(awk '/if_csrf/{print $7}' jar.txt)
curl -k -b jar.txt https://<host>/users/me
curl -k -b jar.txt -H "X-CSRF-Token: $CSRF" -X POST https://<host>/api/alert-rules -d '{...}'

# API/service client: bearer token (no cookies, no CSRF)
TOKEN=$(curl -sk -X POST https://<host>/auth/jwt/login \
  -d 'username=svc@example.com&password=<password>' | jq -r .access_token)
curl -k https://<host>/api/measurements/latest -H "Authorization: Bearer $TOKEN"
```

## Configuration

```bash
JWT_SECRET_KEY=<secret>                  # required, no default
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
COOKIE_SECURE=true                       # true wherever served over HTTPS
COOKIE_SAMESITE=lax
INTERNAL_SERVICE_TOKEN=<secret>          # service-to-service (alert worker -> ML inference)
```

Internal service-to-service calls (the alert worker calling ML inference) authenticate with
the shared `INTERNAL_SERVICE_TOKEN` via an `X-Internal-Service-Token` header, failing closed
if it is unset.
