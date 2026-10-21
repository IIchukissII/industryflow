<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Authentication

How users, API clients and services authenticate to the API gateway: platform sessions, tokens and
revocation ([ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md)).

| Domain | Mechanism | Page |
|---|---|---|
| Browser (platform session) | Access token in httpOnly cookies + double-submit CSRF | This page |
| API client | Access token as `Authorization: Bearer` | This page |
| Service to service (alert worker → `ml-service-api`) | `X-Internal-Service-Token` header | This page |
| Device / gateway ingestion | mTLS client certificate from the Device CA | [device mTLS](device-mtls.md) |
| Transport | HTTPS / `wss://`, terminated at the TLS edge | [TLS](tls.md) |

## Browser cookies

`POST /auth/login` sets:

| Cookie | Contents | Flags | Path |
|---|---|---|---|
| `if_access` | Access JWT | httpOnly, Secure, SameSite=`COOKIE_SAMESITE` | `/` |
| `if_refresh` | Refresh token | httpOnly, Secure, SameSite=`COOKIE_SAMESITE` | `/auth` |
| `if_csrf` | CSRF token | Secure, readable by JavaScript | `/` |

| Rule | Detail |
|---|---|
| CSRF | On `POST`/`PUT`/`PATCH`/`DELETE` carrying `if_access` and no `Authorization` header, `X-CSRF-Token` must equal `if_csrf`; else **403**. `/auth/*` is exempt |
| Bearer clients | No cookies, no CSRF check |
| `COOKIE_SECURE=false` | Plain-HTTP development only |

## Websockets

`/ws/sensors` and `/ws/sensors/{equipment_id}` authenticate from the `if_access` cookie on the
handshake. A token in the URL is not accepted.

| Check | Result |
|---|---|
| `Origin` present and neither in `CORS_ORIGINS` nor equal to the addressed host | Close **1008** before authentication |
| Access token missing or expired, or its session ended (logout, or all sessions ended) | Close **4401**; the client refreshes via `/auth/refresh` and reconnects |
| User inactive, deactivated or deleted; no company; company suspended | Close **1008** |
| Re-check of an open socket | Every `WS_SESSION_RECHECK_SECONDS` |

`CORS_ORIGINS` lists the frontend's public origin with its port (`https://<host>:<FRONTEND_HTTPS_PORT>`).

## Tokens

| Token | Form | Lifetime | Carried as |
|---|---|---|---|
| Access | JWT, HS256, signed with `JWT_SECRET_KEY`; claims `company_id`, `role`, `sid`, `iat` | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `if_access` cookie or `Authorization: Bearer` |
| Refresh | Opaque random string in Redis, bound to a session id and a fingerprint of the password hash | `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `if_refresh` cookie or the `/auth/refresh` body |

Downstream services read the tenant from the `company_id` claim
([ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)). The claims are set by
`CompanyClaimJWTStrategy` in `services/api_gateway/users.py`.

## Revocation

| Event | Effect |
|---|---|
| Refresh | The token is claimed atomically (single use) and replaced; a used or unknown token is rejected |
| Logout | That session's refresh token is revoked, the session is recorded as ended; the user's capability handles, notebook and model upload, are revoked ([ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md)) |
| Password change or reset, user deactivated or deleted, tenant deactivated | All of the user's sessions and capability handles are revoked |
| Password set outside the gateway (`manage_users.py`) | Existing sessions fail at their next refresh (fingerprint mismatch) |

Session-ended records live for one access-token lifetime; open sockets read them at re-check.

## Suspended tenants

A user of an inactive tenant is refused at sign-in, on every session-authenticated request, at
refresh, on a socket and on a capability handle
([ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md)).

| Element | Behaviour |
|---|---|
| Gateway cache | Per-process, `COMPANY_STATUS_CACHE_SECONDS`; the replica that changes the flag sees it at once |
| Suspension marker | `nbcap-suspended:<company_id>` in the capability store, read by the SQL proxy, tracking gateway, cold-store broker, notebook hub and upload minter |
| Devices | Refused at ingestion (HTTP 403) within `TENANT_STATUS_PUBLISH_SECONDS` (10 s), from the `tenant-status` stream ([stream processing](../architecture/stream-processing.md#kafka-topics)); counter `ingestion_suspended_tenant_refusals_total` |
| Writers of tenant data | Read the registry, not the marker: the stream jobs dead-letter the tenant's readings at once (`suspended_tenant`); the alert worker drops its rules at the next reload (`RULE_RELOAD_INTERVAL`) |
| Reactivation | Removes the marker; restores no session |

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/auth/login` | Form `username` (email), `password` | Sets the cookies; returns `access_token` and `refresh_token` |
| POST | `/auth/refresh` | Refresh token (cookie or body) | Rotates the session |
| POST | `/auth/logout` | Refresh token | Revokes it, clears the cookies (204) |
| POST | `/auth/jwt/login` | Form `username`, `password` | Access token only (fastapi-users) |
| GET | `/auth/verify` | session | 204 with `X-IF-User` / `X-IF-Company-Id` / `X-IF-Role` for the notebook SSO proxy ([ADR-0014](../../ADR/ADR-0014-notebook-hub-single-sign-on.md)) |
| GET, PATCH | `/users/me` | session | Current user; change own email or password |
| GET, POST, PATCH, DELETE | `/api/users[/{id}]` | `admin` | [User management](user-management.md) |

```bash
# Browser-style: cookies + CSRF
curl -k -c jar.txt -X POST https://<host>/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=user@example.com&password=<password>'
CSRF=$(awk '/if_csrf/{print $7}' jar.txt)
curl -k -b jar.txt https://<host>/users/me
curl -k -b jar.txt -H "X-CSRF-Token: $CSRF" -X POST https://<host>/api/alert-rules -d '{...}'

# API client: bearer token
TOKEN=$(curl -sk -X POST https://<host>/auth/jwt/login \
  -d 'username=svc@example.com&password=<password>' | jq -r .access_token)
curl -k https://<host>/api/measurements/latest -H "Authorization: Bearer $TOKEN"
```

## Configuration

| Name | Default | Meaning |
|---|---|---|
| `JWT_SECRET_KEY` | none (required) | Access-token signing key |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access-token lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh-token lifetime |
| `COOKIE_SECURE` | `true` | `Secure` flag on the cookies |
| `COOKIE_SAMESITE` | `lax` | `SameSite` on the cookies |
| `CORS_ORIGINS` | none (required) | Allowed origins, comma-separated; also the websocket `Origin` allowlist |
| `WS_SESSION_RECHECK_SECONDS` | `60` | Open-socket session re-check |
| `COMPANY_STATUS_CACHE_SECONDS` | `30` | Tenant active-flag cache |
| `INTERNAL_SERVICE_TOKEN` | none | Shared secret for `X-Internal-Service-Token` (alert worker → `/api/inference/predict`, `/api/drift/evaluate`); unset, `ml-service-api` refuses internal calls |
| `NOTEBOOK_SSO_PROXY_SECRET` | none (required, notebooks) | Shared secret for `X-IF-SSO-Proxy-Secret` (notebook SSO proxy → hub); the hub honours the `X-IF-*` identity only with it and does not start without it |
