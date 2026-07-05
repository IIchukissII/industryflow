<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# notebook_sql_proxy

The trusted SQL access proxy for authoring notebooks (ADR-0015 dec 5-6). A kernel connects with
its **SQL-audience capability handle** in place of a database password; the proxy resolves the
handle to its tenant, opens a backend connection as the **privileged principal**, assumes that
tenant's **read-only role** (`SET ROLE tenant_reader_<uuid>`, the ADR-0011 keystone), and only
then relays the kernel's queries. The kernel never holds a database password (ADR-0012 dec 2),
and a cross-tenant query fails at the GRANT level.

## Contents

- **`binding.py`** (pure, tested) — resolve an SQL-audience handle to its tenant, derive the
  per-tenant reader role, and build the per-connection setup statements.
- **`proxy.py`** (tested orchestration) — authorize a connection, bind it to its tenant, relay;
  written against a `Backend` protocol so the policy is testable with a fake backend. On a denied
  handle **no backend is opened** — the privileged principal is never used for an unresolved handle.
- **`wire.py`** (pure, tested) — the PostgreSQL v3 message codec: parse the StartupMessage /
  SSLRequest, build the `Authentication*` messages, read the PasswordMessage, build
  `ErrorResponse` / `ReadyForQuery` / `Query`, and frame regular messages.
- **`scram.py`** (pure, tested) — the SCRAM-SHA-256 client used to authenticate the **upstream**
  privileged connection (`scram-sha-256` is the ADR-0017 method). Validated against the RFC 7677
  test vector, so the crypto is correct independent of a live database.
- **`server.py`** — the wire entry point. `authenticate_client` runs the kernel-facing handshake
  (decline SSL on the pod-internal hop, read the StartupMessage, request a cleartext password,
  extract the capability **handle**) and feeds it to `proxy.serve_connection`. `PostgresBackend`
  opens the upstream connection — **TLS to the hardened DB** (`build_upstream_ssl_context` /
  `open_upstream`: SSLRequest negotiation + `verify-full` against the internal CA, ADR-0017) —
  assumes the tenant role, completes the kernel handshake, and relays bytes both ways.
- **`tests/`** — run in the `unit-tests` CI workflow, incl. `test_tls_integration.py` (a fake
  Postgres-TLS server with a throwaway trustme cert — no real DB needed). `test_sql_proxy_integration.py`
  is the live end-to-end proof (issue #19); it **skips** unless a real notebooks-profile stack is
  reachable, so it is inert in the unit run.

## Status & boundary

**Verified (unit-tested, no cluster):** the binding policy, the connection orchestration, the
wire codec, the SCRAM-SHA-256 client (RFC 7677 vector), the **frontend handshake** (kernel-facing
auth extracting the capability handle, incl. the deny path → `FATAL` `ErrorResponse`, **no backend
opened**), and the **upstream-TLS handshake** — against a fake Postgres-TLS server: `verify-full`
connects and sends the StartupMessage encrypted, an untrusted server cert is rejected, and a
server that declines TLS is refused.

**NOT cluster-validated:** the live `PostgresBackend` relay against a **real Postgres** — the
upstream SCRAM exchange, the `SET ROLE` setup, and the bidirectional byte pipe end-to-end. The
privileged login role must be a member of every `tenant_reader_<uuid>` with `NOINHERIT` (ADR-0015
dec 5; provisioned by `13-notebook-sql-proxy-role.sh`). The end-to-end proof
(`test_sql_proxy_integration.py` — valid handle reads its tenant read-only; cross-tenant denied;
revoked/expired refused; wrong-audience refused) is **written** but awaits a live run on a real
notebooks-profile stack; this is the remaining work in **issue #19**.

## Develop

```bash
pip install -r requirements-dev.txt
pytest tests/
```

## Run the end-to-end proof (issue #19)

The live proof drives the real relay and needs a running **notebooks-profile** stack: TimescaleDB
with the init scripts **and** the `notebook_sql_proxy` privileged role (set
`NOTEBOOK_SQL_PROXY_DB_PASSWORD` so `13-notebook-sql-proxy-role.sh` creates it), Redis, and this
proxy on `:6432`. It provisions two throwaway tenants, mints capability handles into Redis, and
connects a Postgres client **through the proxy** (the handle in place of the password).

```bash
# bring up the notebooks profile (timescaledb + redis + notebook-sql-proxy)
docker compose --profile notebooks up -d --wait timescaledb redis notebook-sql-proxy

pip install -r requirements-dev.txt -r tests/requirements-integration.txt
PGHOST=localhost PGPASSWORD=<db-password> \
  PROXY_HOST=localhost PROXY_PORT=6432 \
  SQL_PROXY_REDIS_URL=redis://localhost:6379/0 \
  pytest tests/test_sql_proxy_integration.py -v
```

It **skips** (never fails) when the DB, Redis, the proxy role, or the proxy port is unreachable, so
it is safe to leave in the default `pytest tests/` run.
