<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# notebook_sql_proxy

The SQL proxy for authoring notebooks ([ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) dec 5-6): a kernel connects with its SQL-audience capability handle as the password, and the proxy relays its session as that tenant's own login after `SET ROLE tenant_reader_<uuid>`. The kernel holds no database password ([ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md) dec 2).

## Database principals

The relay is byte-transparent (`SET ROLE`, `RESET ROLE`, `set_config('role', …)`,
`SET SESSION AUTHORIZATION` pass through), so the database confines the session user; no statement
filtering is relied on.

| Principal | Kind | Reach |
|---|---|---|
| `tenant_sqlproxy_<uuid>` | `LOGIN NOINHERIT`, one per tenant; the only user a kernel session is opened as | `SET ROLE` into its own `tenant_reader_<uuid>`; member of the privilege-free `notebook_sql_tenants` (named in `pg_hba.conf`); `RESET ROLE` reads nothing |
| `notebook_sql_proxy` | Enrolment principal | Member of nothing; `EXECUTE` on `notebook_sql_proxy_enrol()` only; never carries a kernel session |

| Mechanism | Behaviour |
|---|---|
| Tenant login password | `HMAC-SHA256(SQL_PROXY_TENANT_LOGIN_KEY, context ‖ tenant)`, base64url; the database holds only the SCRAM verifier |
| Enrolment | When the database refuses a tenant login (never enrolled, or key rotated), the proxy opens a session as `notebook_sql_proxy`, calls `notebook_sql_proxy_enrol(<tenant>, <verifier>)`, closes it, retries once |
| Revocation | Every `SQL_PROXY_REVALIDATE_SECONDS` each session re-resolves its handle; a handle that no longer resolves to the same binding, or a store that cannot answer, closes the session |
| Cancellation | The kernel gets a proxy-issued `BackendKeyData`; a `CancelRequest` quoting it is forwarded to the upstream session over TLS. Keys are process-local: one replica |

The roles and functions are created by
[`13-notebook-sql-proxy-role.sh`](../../infrastructure/timescaledb/init-scripts/13-notebook-sql-proxy-role.sh).

## Files

| File | Content |
|---|---|
| `binding.py` | Resolve a handle to its tenant (malformed entries deny); tenant login name, password, reader role; enrolment and per-connection setup statements |
| `proxy.py` | Authorize, bind, relay with live re-check, against a `Backend` protocol; a denied handle opens no backend |
| `wire.py` | PostgreSQL v3 message codec, incl. `CancelRequest`, `BackendKeyData` |
| `scram.py` | SCRAM-SHA-256 client ([ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md)), checked against the RFC 7677 vector; stored-verifier computation |
| `server.py` | Kernel-facing handshake, `UpstreamConnection` (TLS `verify-full`, SCRAM, startup), enrolment, `CancelRegistry`, `PostgresBackend` |

| Test | Runs in |
|---|---|
| `tests/test_*.py` except the integration file | `unit-tests.yml` |
| `tests/test_backend.py` | Enrolment, cancellation, revocation against a fake Postgres over real sockets |
| `tests/test_tls_integration.py` | Fake Postgres-TLS server with a `trustme` cert |
| `tests/test_sql_proxy_integration.py` | `db-tenant-isolation.yml`; live end-to-end proof |

## Settings

| Env var | Default | Meaning |
|---|---|---|
| `SQL_PROXY_HOST` | `0.0.0.0` | Listen address |
| `SQL_PROXY_PORT` | `6432` | Listen port |
| `SQL_PROXY_DB_HOST` | required | Database host |
| `SQL_PROXY_DB_PORT` | `5432` | Database port |
| `SQL_PROXY_DB_NAME` | `industryflow` | Database name |
| `SQL_PROXY_DB_USER` | required | Enrolment principal (`notebook_sql_proxy`) |
| `SQL_PROXY_DB_PASSWORD` | required | Its password |
| `SQL_PROXY_TENANT_LOGIN_KEY` | required, ≥ 32 characters | Derives every tenant login's password |
| `SQL_PROXY_DB_SSLMODE` | `verify-full` | Upstream TLS mode |
| `SQL_PROXY_DB_SSLROOTCERT` | unset | Internal CA bundle |
| `SQL_PROXY_REDIS_URL` | required | Capability store |
| `SQL_PROXY_REVALIDATE_SECONDS` | `15` | Session re-check interval |

## Commands

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

## Existing databases

Init scripts run on an empty data volume only. On an existing database:

| Step | Command |
|---|---|
| Re-run the principals script (idempotent; removes tenant-reader memberships from `notebook_sql_proxy`, creates missing tenant logins) | `docker compose exec timescaledb bash /docker-entrypoint-initdb.d/13-notebook-sql-proxy-role.sh` |
| Load the `+notebook_sql_tenants` rule in `pg_hba.conf` | `docker compose exec timescaledb psql -U <db-user> -d <db-name> -c 'SELECT pg_reload_conf()'` |
| Set `NOTEBOOK_SQL_PROXY_TENANT_KEY` and recreate the proxy | each tenant login is enrolled on its first connection |
