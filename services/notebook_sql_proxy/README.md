<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# notebook_sql_proxy

The trusted SQL access proxy for authoring notebooks (ADR-0015 dec 5-6). A kernel connects with
its **SQL-audience capability handle** in place of a database password; the proxy resolves the
handle to its tenant, opens a backend connection **as that tenant's own login**, assumes the
tenant's **read-only role** (`SET ROLE tenant_reader_<uuid>`, the ADR-0011 keystone), and only
then relays the kernel's queries. The kernel never holds a database password (ADR-0012 dec 2).

## The boundary is the session user

The relay is byte-transparent: whatever the upstream session user may do, the kernel may do —
`SET ROLE`, `RESET ROLE`, `set_config('role', …)`, `SET SESSION AUTHORIZATION`. So the session a
kernel's bytes reach is opened as a principal the **database** confines to one tenant, and no
statement filtering is relied on:

| Principal | What it is | What it can reach |
|---|---|---|
| `tenant_sqlproxy_<uuid>` | one `LOGIN NOINHERIT` role per tenant; the only user a kernel's session is opened as | its own `tenant_reader_<uuid>` (via `SET ROLE`) and the privilege-free `notebook_sql_tenants` group that pg_hba names — nothing else; `RESET ROLE` leaves it reading nothing |
| `notebook_sql_proxy` | the enrolment principal | membership in nothing; `EXECUTE` on `notebook_sql_proxy_enrol()` only. Never carries a kernel's session |

A tenant login's password is `HMAC-SHA256(SQL_PROXY_TENANT_LOGIN_KEY, tenant)`. The key is held
only by the proxy; the database holds only the SCRAM verifier. When the database refuses a tenant
login (never enrolled, or the key rotated), the proxy opens a short session as
`notebook_sql_proxy`, runs `notebook_sql_proxy_enrol(<tenant>, <verifier>)`, closes it, and retries
once. The roles, the functions and the migration from the earlier single-privileged-role design
are in `infrastructure/timescaledb/init-scripts/13-notebook-sql-proxy-role.sh`.

## Revocation and cancellation

- **Revocation reaches open sessions.** Every `SQL_PROXY_REVALIDATE_SECONDS` (default 15) each
  session re-resolves its handle; once it no longer resolves to the same binding (revoked,
  expired, re-bound) — or the store cannot answer — the proxy closes the session (ADR-0012 dec 6).
- **Notebook interrupts cancel queries.** The kernel is given a proxy-issued `BackendKeyData`
  (never the database's own); a `CancelRequest` quoting it is mapped to the upstream session and
  forwarded over TLS. Keys are process-local, so the proxy runs as one replica.

## Contents

- **`binding.py`** (pure, tested) — resolve an SQL-audience handle to its tenant (malformed store
  entries deny), derive the tenant login, its password and its reader role, build the enrolment
  statement and the per-connection setup statements.
- **`proxy.py`** (tested orchestration) — authorize a connection, bind it to its tenant, relay with
  a live re-check; written against a `Backend` protocol. On a denied handle **no backend is opened**.
- **`wire.py`** (pure, tested) — the PostgreSQL v3 message codec, incl. `CancelRequest` and
  `BackendKeyData`.
- **`scram.py`** (pure, tested) — the SCRAM-SHA-256 client used upstream (`scram-sha-256` is the
  ADR-0017 method), validated against the RFC 7677 vector, and the stored-verifier computation the
  proxy enrols.
- **`server.py`** — the wire entry point: the kernel-facing handshake, `UpstreamConnection` (TLS
  `verify-full` against the internal CA, SCRAM, startup), enrolment, `CancelRegistry`, and
  `PostgresBackend` (tenant login, setup, relay, re-check).
- **`tests/`** — run in the `unit-tests` CI workflow, incl. `test_backend.py` (enrolment,
  cancellation and revocation against a fake Postgres over real sockets) and
  `test_tls_integration.py` (a fake Postgres-TLS server with a throwaway trustme cert).
  `test_sql_proxy_integration.py` is the live end-to-end proof; it runs in `db-tenant-isolation`.

## Configuration

| Env | Meaning |
|---|---|
| `SQL_PROXY_DB_HOST` / `_PORT` / `_NAME` | the database |
| `SQL_PROXY_DB_USER` / `_PASSWORD` | the enrolment principal (`notebook_sql_proxy`) |
| `SQL_PROXY_TENANT_LOGIN_KEY` | derives every tenant login's password; at least 32 characters |
| `SQL_PROXY_DB_SSLMODE` / `_SSLROOTCERT` | upstream TLS (default `verify-full`) |
| `SQL_PROXY_REDIS_URL` | the capability store |
| `SQL_PROXY_REVALIDATE_SECONDS` | how often an open session re-checks its handle (default 15) |

## Develop

```bash
pip install -r requirements-dev.txt
pytest tests/ --ignore=tests/test_sql_proxy_integration.py
```

## Run the end-to-end proof

The live proof drives the real relay and needs a running **notebooks-profile** stack: TimescaleDB
with the init scripts **and** the notebooks principals (set `NOTEBOOK_SQL_PROXY_DB_PASSWORD` so
`13-notebook-sql-proxy-role.sh` creates them), Redis, and this proxy on `:6432`. It provisions two
throwaway tenants, mints capability handles into Redis, and connects a Postgres client **through
the proxy** (the handle in place of the password).

```bash
docker compose -f docker-compose.yml -f docker-compose.ci-notebooks.yml \
  --profile notebooks up -d --build timescaledb redis notebook-sql-proxy

pip install -r requirements-dev.txt -r tests/requirements-integration.txt
PGHOST=localhost PGPASSWORD=<db-password> \
  PROXY_HOST=localhost PROXY_PORT=6432 \
  SQL_PROXY_REDIS_URL=redis://localhost:6379/0 \
  pytest tests/test_sql_proxy_integration.py -v
```

It **skips** when the DB, Redis, the proxy principals, or the proxy port is unreachable, unless
`IF_REQUIRE_LIVE_STACK` is set (CI sets it), in which case a skip is a failure.

## Existing deployments

Init scripts run only on a fresh volume. On an existing database, re-run the principals script
(idempotent) — it revokes every tenant-reader membership the earlier design gave
`notebook_sql_proxy` and creates a login for every existing tenant:

```bash
docker compose exec timescaledb bash /docker-entrypoint-initdb.d/13-notebook-sql-proxy-role.sh
docker compose exec timescaledb psql -U <db-user> -d <db-name> -c 'SELECT pg_reload_conf()'
```

The reload picks up the pg_hba rule for `+notebook_sql_tenants`. Set
`NOTEBOOK_SQL_PROXY_TENANT_KEY` for the proxy, then recreate it; each tenant login is enrolled on
its first connection.
