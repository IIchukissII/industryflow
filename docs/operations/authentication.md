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
requests — bearer/API clients are exempt, and `/auth/*` is exempt. The websocket
authenticates from the same `if_access` cookie on the `wss://` handshake (no token in the URL).

`Secure` cookies require HTTPS; set `COOKIE_SECURE=true` in any deployed environment.

## Tokens

| Token | Form | Lifetime | Carried as |
|-------|------|----------|-----------|
| **Access** | JWT (HS256), signed by `JWT_SECRET_KEY` | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (default **30 min**) | `if_access` cookie (browser) **or** `Authorization: Bearer` (API/service) |
| **Refresh** | opaque random string in Redis | `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (default **7 days**) | `if_refresh` cookie, or the `/auth/refresh` body |

The access JWT carries **`company_id` and `role` claims** (`CompanyClaimJWTStrategy` in
`services/api_gateway/users.py`); downstream services read the tenant from the claim rather
than looking it up (**[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**).
Refresh tokens are **single-use and rotated** on every refresh (reuse is detected and
rejected) and are **revoked server-side** on logout.

## Endpoints (API Gateway)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/auth/login` | Authenticate (form: `username`=email, `password`) → sets auth cookies; also returns the tokens for API clients |
| `POST` | `/auth/refresh` | Rotate the session (refresh from cookie or body) |
| `POST` | `/auth/logout` | Revoke the refresh token and clear the cookies (204) |
| `POST` | `/auth/jwt/login` | fastapi-users login — access token only (kept for compatibility) |
| `POST` | `/auth/register` | Register a user (needs `company_id` + `role`) |
| `GET`  | `/users/me` | Current user |

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
