<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# SQL proxy

The manual of [ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-the-sql-access-proxy.md), [ADR-0012](../../ADR/ADR-0012-per-session-credential-delivery-for-notebook-environments.md), [ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md), [ADR-0042](../../ADR/ADR-0042-capability-use-is-recorded-by-the-planes-that-resolve-it-into-one-durable-record.md): the SQL proxy for authoring notebooks, which relays a kernel's session, authenticated by its SQL-audience capability handle, as that tenant's own login after `SET ROLE tenant_reader_<uuid>`.

## ADR-0015 dec 5, 6

The relay is byte-transparent (`SET ROLE`, `RESET ROLE`, `set_config('role', …)`,
`SET SESSION AUTHORIZATION` pass through), so the database confines the session user; no statement
filtering is relied on. Each kernel connection is one upstream session.

| Code | Role |
|---|---|
| `services/notebook_sql_proxy/proxy.py` | Authorize, record, bind, relay with live re-check, against a `Backend` protocol; a denied handle opens no backend |
| `services/notebook_sql_proxy/binding.py::tenant_login_password` | Tenant login password from the proxy-held key |
| `services/notebook_sql_proxy/binding.py` | Resolve a handle to its tenant and refusal reason (malformed entries deny); tenant login name, reader role; enrolment and per-connection setup statements |
| `services/notebook_sql_proxy/wire.py` | PostgreSQL v3 message codec, incl. `CancelRequest`, `BackendKeyData` |
| `services/notebook_sql_proxy/server.py` | Kernel-facing handshake, `UpstreamConnection`, enrolment, `CancelRegistry`, `PostgresBackend` |
| `infrastructure/timescaledb/init-scripts/13-notebook-sql-proxy-role.sh` | Creates the principals and `notebook_sql_proxy_enrol()` |

| Principal | Kind | Reach |
|---|---|---|
| `tenant_sqlproxy_<uuid>` | `LOGIN NOINHERIT`, one per tenant; the only user a kernel session is opened as | `SET ROLE` into its own `tenant_reader_<uuid>`; member of the privilege-free `notebook_sql_tenants` (named in `pg_hba.conf`); `RESET ROLE` reads nothing |
| `notebook_sql_proxy` | Enrolment principal | Member of nothing; `EXECUTE` on `notebook_sql_proxy_enrol()` only; never carries a kernel session |

| Mechanism | Behaviour |
|---|---|
| Tenant login password | `HMAC-SHA256(SQL_PROXY_TENANT_LOGIN_KEY, context ‖ tenant)`, base64url; the database holds only the SCRAM verifier |
| Enrolment | When the database refuses a tenant login (never enrolled, or key rotated), the proxy opens a session as `notebook_sql_proxy`, calls `notebook_sql_proxy_enrol(<tenant>, <verifier>)`, closes it, retries once |
| Cancellation | The kernel gets a proxy-issued `BackendKeyData`; a `CancelRequest` quoting it is forwarded to the upstream session over TLS. Keys are process-local: one replica |

### Settings

| Name | Default | Meaning |
|---|---|---|
| `SQL_PROXY_HOST` | `0.0.0.0` | Listen address |
| `SQL_PROXY_PORT` | `6432` | Listen port |
| `SQL_PROXY_DB_HOST` | required | Database host |
| `SQL_PROXY_DB_PORT` | `5432` | Database port |
| `SQL_PROXY_DB_NAME` | `industryflow` | Database name |
| `SQL_PROXY_DB_USER` | required | Enrolment principal (`notebook_sql_proxy`) |
| `SQL_PROXY_DB_PASSWORD` | required | Its password |
| `SQL_PROXY_TENANT_LOGIN_KEY` | required, ≥ 32 characters | Derives every tenant login's password |
| `SQL_PROXY_REDIS_URL` | required | Capability store |

### Tests

| Test | Runs in |
|---|---|
| `services/notebook_sql_proxy/tests/test_*.py` except the integration file | `unit-tests.yml` |
| `services/notebook_sql_proxy/tests/test_backend.py` | Enrolment, cancellation, revocation against a fake Postgres over real sockets |
| `services/notebook_sql_proxy/tests/test_tls_integration.py` | Fake Postgres-TLS server with a `trustme` cert |
| `services/notebook_sql_proxy/tests/test_sql_proxy_integration.py` | `db-tenant-isolation.yml`; live end-to-end proof |

### Commands

| Task | Command |
|---|---|
| Install test deps | `pip install ../../libs/industryflow_contracts -r requirements-dev.txt` |
| Unit tests | `pytest tests/ --ignore=tests/test_sql_proxy_integration.py` |
| Start the live stack (repo root) | `docker compose -f docker-compose.yml -f docker-compose.ci-notebooks.yml --profile notebooks up -d --build timescaledb redis notebook-sql-proxy` |
| Integration test deps | `pip install ../../libs/industryflow_contracts -r requirements-dev.txt -r tests/requirements-integration.txt` |
| Integration test | `PGHOST=localhost PGPASSWORD=<db-password> PROXY_HOST=localhost PROXY_PORT=6432 SQL_PROXY_REDIS_URL=redis://localhost:6379/0 pytest tests/test_sql_proxy_integration.py -m integration -v` |

The live stack needs `NOTEBOOK_SQL_PROXY_DB_PASSWORD` set so script 13 creates `notebook_sql_proxy`.
The test provisions two throwaway tenants, mints handles into Redis and connects through the proxy.
It skips when the database, Redis, the principals or the proxy port is unreachable; with
`IF_REQUIRE_LIVE_STACK` set (CI sets it) a skip is a failure.

### Existing databases

On an existing database ([init scripts](../../infrastructure/timescaledb/init-scripts/README.md#execution)):

| Step | Command |
|---|---|
| Re-run the principals script (idempotent; removes tenant-reader memberships from `notebook_sql_proxy`, creates missing tenant logins) | `docker compose run --rm db-migrate reapply 13-notebook-sql-proxy-role.sh` |
| Load the `+notebook_sql_tenants` rule in `pg_hba.conf` | `docker compose exec timescaledb psql -U <db-user> -d <db-name> -c 'SELECT pg_reload_conf()'` |
| Set `NOTEBOOK_SQL_PROXY_TENANT_KEY` and recreate the proxy | each tenant login is enrolled on its first connection |

## ADR-0015 dec 2

Every `SQL_PROXY_REVALIDATE_SECONDS` each session re-resolves its handle; a handle that no longer
resolves to the same binding, or a store that cannot answer, closes the session.

| Name | Default | Meaning |
|---|---|---|
| `SQL_PROXY_REVALIDATE_SECONDS` | `15` | Session re-check interval |

| Code | Role |
|---|---|
| `services/notebook_sql_proxy/proxy.py::serve_connection` | Relay with the periodic re-check |

## ADR-0012 dec 2, 4

The kernel presents only its capability handle as the password; it holds no database password.

| Code | Role |
|---|---|
| `services/notebook_sql_proxy/server.py::authenticate_client` | Takes the handle as the kernel's password |

## ADR-0017 dec 1, 2

The upstream connection verifies the server and authenticates by SCRAM-SHA-256.

| Name | Default | Meaning |
|---|---|---|
| `SQL_PROXY_DB_SSLMODE` | `verify-full` | Upstream TLS mode |
| `SQL_PROXY_DB_SSLROOTCERT` | unset | Internal CA bundle |

| Code | Role |
|---|---|
| `services/notebook_sql_proxy/server.py::build_upstream_ssl_context` | TLS context, hostname checked |
| `services/notebook_sql_proxy/scram.py` | SCRAM-SHA-256 client, checked against the RFC 7677 vector; stored-verifier computation |

## ADR-0042 dec 1-3, 5, 8, 9

The proxy appends its events to the capability store's `nbcap-audit` stream; it holds no database
principal for them. Statements are not recorded. A first use or refusal that cannot be recorded refuses
the connection; a refusal is also logged, without the handle.

| Event | Recorded |
|---|---|
| `first_use` | Once per handle at this plane |
| `refused` | A real entry's refusal once per entry and reason in a window, with the count of the rest; a value naming no entry by a short digest, once per window, up to a bound per source address |
| `sql_session_opened`, `sql_session_closed` | Once per handle and kind in a window, with the count of the rest; close reason `client_closed`, `capability_revoked`, `recheck_failed` or `error`; duration in `detail` |

| Code | Role |
|---|---|
| `services/notebook_sql_proxy/binding.py::record_resolution` | First use and refusals |
| `services/notebook_sql_proxy/binding.py::record_session` | Session opening and closing |
