<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Authentication

IndustryFlow has two authentication domains (see **[ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md)**):

- **Human / service API access** — JWT bearer tokens (this document).
- **Device / gateway ingestion** — mutual TLS with client certificates from an
  IndustryFlow-run CA. See ADR-0002 and the PKI record **[ADR-0007](../../ADR/ADR-0007-public-key-infrastructure.md)**.

The API auth model is recorded in **[ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md)**.

## Tokens

| Token | Form | Lifetime | Carried in |
|-------|------|----------|------------|
| **Access** | JWT (HS256), signed by `JWT_SECRET_KEY` | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (default **30 min**) | `Authorization: Bearer <token>` |
| **Refresh** | opaque random string, stored server-side in Redis | `JWT_REFRESH_TOKEN_EXPIRE_DAYS` (default **7 days**) | request body of `/auth/refresh` |

The **access JWT carries `company_id` and `role` claims** (`CompanyClaimJWTStrategy` in
`services/api_gateway/users.py`). Downstream services read the tenant directly from the
`company_id` claim instead of looking it up — see **[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**.

Refresh tokens are **single-use and rotated** on every refresh (reuse is detected and
rejected) and can be **revoked server-side** on logout — they live in Redis, so a session
can be ended before the access token would expire.

## Endpoints (API Gateway, port 8000)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/auth/login` | Authenticate (form: `username`=email, `password`) → `{access_token, refresh_token}` |
| `POST` | `/auth/refresh` | `{refresh_token}` → new `{access_token, refresh_token}` (rotates) |
| `POST` | `/auth/logout` | `{refresh_token}` → revoke (204) |
| `POST` | `/auth/jwt/login` | fastapi-users login — access token only (kept for backward compatibility) |
| `POST` | `/auth/register` | Register a user |
| `GET`  | `/users/me` | Current user |

```bash
# Login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=user@example.com&password=<password>'
# -> {"access_token": "...", "refresh_token": "...", "token_type": "bearer"}

# Use the access token
curl http://localhost:8000/api/measurements/latest -H "Authorization: Bearer <access_token>"

# Refresh when the access token nears expiry
curl -X POST http://localhost:8000/auth/refresh \
  -H "Content-Type: application/json" -d '{"refresh_token": "<refresh_token>"}'
```

## Configuration

```bash
JWT_SECRET_KEY=<secret>                  # required, no default
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
INTERNAL_SERVICE_TOKEN=<secret>          # service-to-service (alert worker -> ML inference)
```

Internal service-to-service calls (the alert worker calling ML inference) authenticate
with the shared `INTERNAL_SERVICE_TOKEN` via an `X-Internal-Service-Token` header, and fail
closed if it is unset.

## Status & roadmap

- The refresh-token flow (`/auth/login`, `/auth/refresh`, `/auth/logout`) is **recently
  added and pending end-to-end validation** against a running gateway + Redis.
- ADR-0004 (decision 3) targets moving browser tokens out of `localStorage` into
  **httpOnly, Secure, SameSite cookies** with CSRF protection, and `wss://` for the
  websocket. Until that lands, the React client stores the access token in `localStorage`
  — a known XSS exposure being addressed.
