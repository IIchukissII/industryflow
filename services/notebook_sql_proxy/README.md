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
  opens the upstream connection, assumes the tenant role, completes the kernel handshake, and
  relays bytes both ways.
- **`tests/`** — run in the `unit-tests` CI workflow.

## Status & boundary

**Verified (unit-tested, no cluster):** the binding policy, the connection orchestration, the
wire codec, the SCRAM-SHA-256 client (RFC 7677 vector), and the **frontend handshake** — the
kernel-facing auth that extracts the capability handle, driven over in-memory pipes, including
the deny path (a refused handle gets a `FATAL` `ErrorResponse` and **no backend is opened**).

**NOT cluster-validated:** `PostgresBackend` — the live upstream connection and the byte relay.
The code is written (upstream SCRAM auth, `SET ROLE` setup over the simple-query protocol,
bidirectional pipe), but it has not run against a real Postgres. The privileged login role must
be a member of every `tenant_reader_<uuid>` with `NOINHERIT` (ADR-0015 dec 5). Completing this —
the end-to-end integration test (valid handle reads its tenant read-only; cross-tenant denied;
revoked/expired refused) and wiring the proxy into Helm/compose — is tracked in **issue #19**.

## Develop

```bash
pip install -r requirements-dev.txt
pytest tests/
```
