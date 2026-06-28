<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# industryflow (Python notebook client)

The blessed, tenant-scoped way to load a tenant's data into a notebook as pandas DataFrames
(ADR-0011 dec 4). It calls the platform's existing read API over HTTP with a per-session
capability minted by the notebook spawner (ADR-0012/0015), sent in the `X-IF-Capability` header.
The data API resolves the capability to the caller's tenant and serves the request read-only. It
never holds a database or object-store credential.

> **Status:** the client and the data-API capability resolution are in place. The spawner that
> mints and injects the handle runs on a cluster (not yet validated end-to-end).

```python
from datetime import datetime, timedelta
from industryflow import IndustryFlowClient

client = IndustryFlowClient("https://api.industryflow.local", token=NOTEBOOK_TOKEN)

# Raw measurements over a window (ascending, ready for plotting)
df = client.measurements(
    sensor_id="…",
    start=datetime.utcnow() - timedelta(hours=6),
    end=datetime.utcnow(),
    order="asc",
)

# Hourly aggregates
hourly = client.aggregations("1hour", equipment_id="…", order="asc")

# A per-equipment training dataset (bulk historical pull)
training = client.training_data("…", lookback_days=30)
```

All calls return only the caller's tenant data — tenant scoping is enforced server-side
(ADR-0003), never by this client.

## Tenant SQL (authoring notebooks)

An authoring notebook can also run SQL against its tenant through the **SQL access proxy**
(ADR-0015). The kernel connects with a normal Postgres driver, presenting its per-session SQL
capability handle as the password — never a database credential, and never the database directly.
The proxy resolves the handle, assumes the tenant's **read-only** role, and relays. Install the
extra (`industryflow[sql]`, already in the authoring image):

```python
from industryflow import IndustryFlowSQL, sql_query

# Reads INDUSTRYFLOW_SQL_PROXY_URL + INDUSTRYFLOW_SQL_CAPABILITY (injected by the hub at spawn).
df = sql_query("SELECT * FROM equipment LIMIT 10")

# Or hold a handle for several queries:
db = IndustryFlowSQL.from_env()
hourly = db.query("SELECT time, value FROM sensor_aggregations_1hour WHERE sensor_id = %s", ["…"])
```

Reads are read-only and single-tenant — enforced by the proxy's `SET ROLE` and the per-tenant
role's grants (ADR-0011), not by this client. SQL is available only in an authoring kernel spawned
by the hub (the env vars above are injected there); elsewhere `from_env()` raises a clear error.

## Develop

```bash
pip install -e ".[dev]"
pytest tests/
```
