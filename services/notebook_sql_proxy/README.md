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
- **`tests/`** — run in the `unit-tests` CI workflow.

## Status & boundary

**Verified:** the binding policy and the connection orchestration (against a fake backend/store).

**NOT implemented / cluster-bound:** the real `Backend` — the Postgres wire. A production entry
point must read the handle from the client's startup/password message, hold the privileged
credential (a login role that is a member of every `tenant_reader_<uuid>`, `NOINHERIT`), open
the backend, run the setup statements, and relay bytes. That needs integration testing against a
real Postgres and is intentionally left out here.

## Develop

```bash
pip install -r requirements-dev.txt
pytest tests/
```
