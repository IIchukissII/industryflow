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
| Access | JWT, HS256, signed with `JWT_SECRET_KEY`; claims `company_id`, `role`, `iat`, and `sid` when issued with a refresh session | `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `if_access` cookie or `Authorization: Bearer` |
| Refresh | Opaque random string in Redis, bound to a session id and a fingerprint of the password hash | `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `if_refresh` cookie or the `/auth/refresh` body |

Downstream services read the tenant from the `company_id` claim
([ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)). The claims are set by
`CompanyClaimJWTStrategy` in `services/api_gateway/users.py`.

## Revocation

| Event | Effect |
|---|---|
| Refresh | The token is claimed atomically (single use) and replaced; an unknown token is rejected (401) |
| A claimed refresh token presented again within `ROTATION_GRACE_SECONDS` (10 s) | Answered with the successor already issued, if still unused (another tab refreshing at once) |
| A claimed refresh token presented again later | **401**; its session is ended: its live refresh token, its access tokens and the user's capability handles |
| Logout (`/auth/logout`, `/auth/jwt/logout`) | The session of the refresh token sent and of the access token presented is recorded as ended, the refresh token revoked; the user's capability handles, notebook and model upload, are revoked ([ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md)) |
| Password change or reset, user moved to another company, deactivated or deleted, tenant deactivated | All of the user's sessions and capability handles are revoked |
| `manage_users.py reset-password` or `delete` | The same; the command exits non-zero if the session store cannot be reached |
| Password set in the database directly | Existing sessions fail at their next refresh (fingerprint mismatch) |

An ended session is recorded in the capability store for one access-token lifetime
(`libs/industryflow_contracts/industryflow_contracts/sessions.py`). Every verifier of an access token
reads the record:

| Verifier | Access token of an ended session | Store unreachable |
|---|---|---|
| API gateway, every request | **401** | **503** |
| `ml-service-api`, every request (`SESSION_REDIS_URL`, required); also **401** when the token's `company_id` is not the account's | **401** | **503** |
| Socket handshake | Close **4401** | Close **4401** |
| Open socket, at re-check | Close **4401** | Checked again next interval |

A read slower than `READ_TIMEOUT_SECONDS` (2 s) counts as unreachable; so does the gateway's read of
the suspension marker, which then leaves the decision to the registry. A token issued up to
`CLOCK_SKEW_SECONDS` (2 s) after an end of all the user's sessions would count as ended, so a sign-in
or refresh in that window waits it out (at most 3 s).

## Sign-in throttling

Password sign-in (`/auth/login`, `/auth/jwt/login`) is slowed, never locked
([ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md) dec 10).

| Limit | Where | Behaviour |
|---|---|---|
| Per account and source | Gateway, `services/api_gateway/sign_in_throttle.py` | `SIGN_IN_THROTTLE=per-source` (compose; needs `EDGE_PROXY_PEERS`, else the gateway refuses to start). From the 5th consecutive failure for one e-mail address from one source (the address the edge reports; IPv6 by its /64), each attempt from it waits 1, 2, 4 … s after the last, up to 300 s; inside the wait **429** with `Retry-After`, the password unchecked and the attempt not counted. Each attempt is counted before the password is checked, so simultaneous guesses pass one at a time. Other sources are not slowed. Unknown addresses are counted the same. An admitted sign-in clears its source; a password change or reset (`manage_users.py reset-password` included) clears every source; an hour without a failure forgets it |
| Per source address | Edge, `EDGE_SIGN_IN_PER_SOURCE=on` (compose) | Burst of 20, then 10 a minute; beyond it **429** |
| Both | Behind a Kubernetes Ingress (the chart: `EDGE_SIGN_IN_PER_SOURCE=off`, `SIGN_IN_THROTTLE=off`) | Not applied: the edge and the gateway see only the Ingress (ADR-0004 Deferred) |

Store unreachable: sign-in is refused with **503**.

## Suspended tenants

A user of an inactive tenant is refused at sign-in, on every session-authenticated request, at
refresh, on a socket and on a capability handle
([ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md)).

| Element | Behaviour |
|---|---|
| Gateway check | The suspension marker in the capability store first (every process refuses a suspension at once), then a per-process cache of the registry (`COMPANY_STATUS_CACHE_SECONDS`), which alone decides when the store is unreachable |
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
| GET, PATCH | `/users/me` | session | Current user (`password_change_required` when held to a change); change own email or password |
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
| `CORS_ORIGINS` | none (required) | Allowed origins, comma-separated, never `*`; also the websocket `Origin` allowlist |
| `WS_SESSION_RECHECK_SECONDS` | `60` | Open-socket session re-check |
| `COMPANY_STATUS_CACHE_SECONDS` | `30` | Tenant active-flag cache: bounds a reactivation, a change made outside the API, and a suspension while the capability store is unreachable |
| `INTERNAL_SERVICE_TOKEN` | none | Shared secret for `X-Internal-Service-Token` (alert worker → `/api/inference/predict`, `/api/drift/evaluate`); unset, `ml-service-api` refuses internal calls |
| `NOTEBOOK_SSO_PROXY_SECRET` | none (required, notebooks) | Shared secret for `X-IF-SSO-Proxy-Secret` (notebook SSO proxy → hub); the hub honours the `X-IF-*` identity only with it and does not start without it |

## ADR-0004 dec 2

An access token carries the tenant claim; a refresh claims its credential atomically; every verifier reads the record of ended sessions.

| Code | Role |
|---|---|
| `services/api_gateway/routers/auth_tokens.py` | Sign-in, refresh rotation, sign-out |
| `libs/industryflow_contracts/industryflow_contracts/sessions.py` | The record of ended sessions every verifier reads |

## ADR-0004 dec 3

The gateway sets the session cookies httpOnly, Secure, SameSite (see Browser cookies).

| Code | Role |
|---|---|
| `services/api_gateway/routers/auth_tokens.py` | Cookie issuance |

## ADR-0004 dec 4

Every gateway route depends on the session principal, except the ones below.

| Unauthenticated route | Exposes |
|---|---|
| `/health` | Gateway and database liveness |
| `/` | API name, version and route index |
| `/auth/login`, `/auth/jwt/login`, `/auth/refresh`, `/auth/logout` | Credential exchange |
| `/ws/sensors[/{equipment_id}]` | Handshake only; the socket authenticates from the cookie before it streams |

There is no public registration route. `ml-service-api` resolves the tenant of an inference request from a user token or the internal service token, never from an unauthenticated body.

| Code | Role |
|---|---|
| `services/api_gateway/main.py` | Router set; no registration router |
| `services/api_gateway/users.py::current_active_user` | Session principal every data route depends on |
| `services/api_gateway/routers/user_admin.py` | Self-service and user administration behind a session |
| `services/ml_service/api/routers/inference.py::_resolve_company_id` | Authenticated tenant for inference |
| `services/api_gateway/tests/test_user_admin.py::test_public_registration_is_gone` | Proof: no registration route |
| `services/api_gateway/tests/test_user_admin.py::test_user_admin_routes_require_a_session` | Proof: user routes answer 401 without a session |

## ADR-0004 dec 5

| Principal | Scope |
|---|---|
| `observer`, `engineer`, `admin` | The tenant of the account's `company_id`; `observer` reads only |
| Superuser (`is_superuser`) | Every tenant and the platform; passes every role gate |

| Rule | Mechanism |
|---|---|
| Tenant of a request | The account's `company_id`, never a request field |
| Tenant admin creates a user | Into the admin's own company; a `company_id` in the body is honoured only for a superuser |
| `company_id`, `is_superuser` on a user | Changed by a superuser only |
| Self-update | Email and password only; unknown fields refused |
| Company create, activation change, delete, deletion request | Superuser only; a tenant admin may rename their own company |
| Seeded superuser | Only with `ADMIN_USER_<n>_SUPERUSER` set; the `admin` role grants none |

| Code | Role |
|---|---|
| `services/api_gateway/dependencies.py::require_role` | Role gate |
| `services/api_gateway/dependencies.py::require_superuser` | Superuser gate |
| `services/api_gateway/dependencies.py::get_writer_with_company` | Writer gate for tenant data |
| `services/api_gateway/schemas.py::AdminUserCreate` | Create body; `company_id` for a superuser only |
| `services/api_gateway/schemas.py::AdminUserUpdate` | Admin update body |
| `services/api_gateway/routers/user_admin.py::_refuse_superuser_only` | Superuser-only fields refused for a tenant admin |
| `services/api_gateway/routers/user_admin.py::_target_in_scope` | Target user inside the admin's tenant |
| `services/api_gateway/routers/companies.py::_require_superuser` | Company provisioning gate |
| `services/api_gateway/routers/companies.py::_assert_own_company` | Tenant admin limited to its own company |
| `services/ml_service/api/auth.py::require_writer` | Writer gate in `ml-service-api` |
| `scripts/seed_users.py` | Superuser only on explicit request |
| `services/api_gateway/tests/test_user_admin.py::test_admin_cannot_touch_another_tenants_user` | Proof: admin stays in its tenant |
| `services/api_gateway/tests/test_companies.py::test_tenant_admin_cannot_provision` | Proof: provisioning is superuser-only |
| `services/api_gateway/tests/test_write_roles.py::test_every_tenant_write_route_is_gated` | Proof: every write route is gated |
| `services/ml_service/tests/test_write_roles.py` | Proof: `observer` gets 403 on `ml-service-api` writes |

## ADR-0004 dec 6

| Channel | Check |
|---|---|
| Startup | A `*` entry in `CORS_ORIGINS` fails the settings while credentials are allowed |
| HTTP | `CORSMiddleware` with `allow_origins` from `CORS_ORIGINS` and credentials allowed |
| Socket handshake | `Origin` absent, in `CORS_ORIGINS`, or equal to the addressed host; else close 1008 |

| Code | Role |
|---|---|
| `services/api_gateway/config.py::Settings` | Refuses a wildcard origin while credentials are allowed |
| `services/api_gateway/main.py` | HTTP origin allowlist |
| `services/ml_service/api/config.py::cors_origins` | ML service: the same refusal |
| `services/ml_service/tests/test_cors_origins.py` | Proof: the ML service refuses a wildcard |
| `services/api_gateway/tests/test_cors_origins.py::test_a_wildcard_origin_is_refused_at_startup` | Proof: a wildcard origin fails startup |
| `services/api_gateway/routers/websocket.py::origin_allowed` | Socket origin check |
| `services/api_gateway/tests/test_websocket_session.py::test_foreign_origin_is_refused_even_with_a_valid_cookie` | Proof: a foreign origin is refused |
| `services/api_gateway/tests/test_websocket_session.py::test_lookalike_origin_is_refused` | Proof: a lookalike origin is refused |

## ADR-0004 dec 7

| Secret | Handling |
|---|---|
| Password-reset and verification tokens | Not logged; only the event is |
| First-run superuser password | Not logged; the log names address, tenant and whether a change is required |
| Access token on a socket | Not accepted in the URL, so it never reaches access logs |

| Code | Role |
|---|---|
| `services/api_gateway/users.py::on_after_forgot_password` | Reset event logged without the token |
| `services/api_gateway/users.py::on_after_request_verify` | Verification event logged without the token |
| `services/api_gateway/first_run.py::ensure_first_superuser` | Creation logged without the password |
| `services/api_gateway/tests/test_websocket_session.py::test_token_in_the_url_is_not_accepted` | Proof: a URL token is refused |

## ADR-0004 dec 9

A live socket authenticates with the session and is closed when the session ends (see Websockets).

| Code | Role |
|---|---|
| `services/api_gateway/routers/websocket.py` | Socket authentication and session checks |

## ADR-0004 dec 10

The limits are described under Sign-in throttling.

| Step | Mechanism |
|---|---|
| Reserve | One atomic store script counts the attempt as a failure, or returns the remaining wait |
| Inside the wait | **429**; the password is not checked |
| Success | Clears the source's count |
| Password change or reset | Clears every source's count for the address |
| Store unreadable | **503** |
| Edge | `limit_req` on the two sign-in paths, keyed by client address when `EDGE_SIGN_IN_PER_SOURCE=on` |

| Code | Role |
|---|---|
| `services/api_gateway/sign_in_throttle.py::RESERVE_SCRIPT` | Atomic count and wait |
| `services/api_gateway/users.py::authenticate` | Throttle before the password check |
| `services/api_gateway/config.py` | `SIGN_IN_THROTTLE` |
| `services/frontend/nginx.conf.template` | Per-source edge limit |
| `services/frontend/docker-entrypoint-edge.sh` | `EDGE_SIGN_IN_PER_SOURCE` validated at start |
| `docker-compose.yml` | Both limits on |
| `deploy/helm/industryflow/templates/_helpers.tpl` | Edge limit off behind the Ingress |
| `deploy/helm/industryflow/values.yaml` | Gateway limit off behind the Ingress |
| `services/api_gateway/tests/test_sign_in_throttle.py::test_one_source_slowing_an_account_does_not_slow_its_owner` | Proof: no lock-out from another source |
| `services/api_gateway/tests/test_sign_in_throttle.py::test_guesses_sent_at_once_are_refused_all_but_one` | Proof: counted before the check |
| `services/api_gateway/tests/test_sign_in_throttle.py::test_inside_the_wait_the_password_is_not_checked` | Proof: refused unchecked |
| `tests/e2e/test_session_end.py::test_wrong_passwords_slow_that_account_and_no_other` | Proof: deployed gateway slows one account only |

## ADR-0004 dec 11

| Step | Mechanism |
|---|---|
| Start | The gateway takes an advisory lock and checks `public."user"`; any row means nothing is created |
| Tenant | The oldest active company, else one provisioned as `PLATFORM_ADMIN_TENANT` (default `Default`) |
| Account | Superuser `PLATFORM_ADMIN_EMAIL` (default `admin@example.com`) with `PLATFORM_ADMIN_PASSWORD` |
| Hold | `password_change_required` is set only when the password is the published default |
| While held | Only `GET`/`PATCH /users/me` and sign-out pass; every other request gets 403 `PASSWORD_CHANGE_REQUIRED`; no socket opens; `ml-service-api` refuses the account |
| Release | A new password, neither the current one nor the default in any case or spacing, clears the hold in the same write |

| Code | Role |
|---|---|
| `services/api_gateway/first_run.py::ensure_first_superuser` | Creation under the lock, only on an empty platform |
| `services/api_gateway/main.py` | First-run task at start; request recorded for the hold check |
| `services/api_gateway/users.py::PASSWORD_CHANGE_ALLOWED` | What a held account may do |
| `services/api_gateway/users.py::validate_password` | The default and the current password refused as the new one |
| `services/api_gateway/models/user.py` | `password_change_required` column |
| `infrastructure/timescaledb/init-scripts/34-user-password-change-required.sql` | Column migration |
| `services/api_gateway/routers/websocket.py` | No socket for a held account |
| `services/ml_service/api/auth.py::_usable_account` | Held account refused by `ml-service-api` |
| `scripts/user_admin_db.py` | The CLI takes the same lock |
| `services/frontend/src/pages/FirstPasswordChange.jsx` | Password-change page for a held account |
| `services/api_gateway/tests/test_first_run.py::test_nothing_is_created_on_a_platform_that_has_any_account` | Proof: populated platform gains nothing |
| `services/api_gateway/tests/test_first_run.py::test_a_password_the_deployment_chose_is_not_forced` | Proof: a chosen password is not held |
| `services/api_gateway/tests/test_first_run.py::test_through_the_app_a_held_account_reads_itself_and_nothing_else` | Proof: the hold through the app |
| `tests/e2e/test_first_run.py::test_a_held_account_must_change_its_password_before_anything_else` | Proof: on the deployed platform |
