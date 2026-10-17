<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# industryflow (Python notebook client)

Tenant-scoped access to a tenant's data from a notebook kernel, returned as pandas DataFrames
([ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) dec 4). The
kernel holds only per-session capability handles minted by the hub
([ADR-0012](../../ADR/ADR-0012-notebook-credential-delivery.md),
[ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md)), never a database or
object-store credential. Tenant scoping is enforced server-side
([ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)), not by this client.

## Exports

| Name | Purpose | Backend | Hub-injected env | Profiles |
|------|---------|---------|------------------|----------|
| `IndustryFlowClient` | Measurements, aggregations, training data | API gateway read API, capability in `X-IF-Capability` | `INDUSTRYFLOW_API_URL`, `INDUSTRYFLOW_API_CAPABILITY` | authoring, analytics |
| `IndustryFlowSQL`, `sql_query` | Read-only tenant SQL | SQL access proxy, capability as the Postgres password | `INDUSTRYFLOW_SQL_PROXY_URL`, `INDUSTRYFLOW_SQL_CAPABILITY` | authoring |
| `IndustryFlowCold`, `cold_read_all` | Cold-layer Parquet history | cold-store broker, pre-signed object-store reads ([ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md)) | `INDUSTRYFLOW_COLD_BROKER_URL`, `INDUSTRYFLOW_COLD_CAPABILITY` | authoring |
| `SKOPS_TRUSTED_TYPES` | Types to pass to `mlflow.sklearn.log_model` for tree-based models ([ADR-0027](../../ADR/ADR-0027-model-artifact-supply-chain-parity.md)) | — | — | authoring |

The SQL, cold and tracking variables are injected only when the hub itself has the matching URL
configured ([`jupyterhub_config.py`](../../services/notebook_hub/jupyterhub_config.py)). Outside a
hub-spawned kernel, `from_env()` raises.

## Read API

```python
import os
from datetime import datetime, timedelta, timezone
from industryflow import IndustryFlowClient

client = IndustryFlowClient(os.environ["INDUSTRYFLOW_API_URL"],
                            token=os.environ["INDUSTRYFLOW_API_CAPABILITY"])

now = datetime.now(timezone.utc)
df = client.measurements(sensor_id="<sensor-id>", start=now - timedelta(hours=6), end=now)
hourly = client.aggregations("1hour", equipment_id="<equipment-id>")
training = client.training_data("<equipment-id>", lookback_days=30)
```

| Method | Endpoint | Defaults |
|--------|----------|----------|
| `measurements(...)` | `GET /api/measurements` | `order="asc"`, `limit=1000` |
| `aggregations(window, ...)` | `GET /api/aggregations/{window}`; `window` is `1min`, `5min` or `1hour` | `order="asc"`, `limit=1000` |
| `training_data(equipment_id, ...)` | `GET /api/training-data/equipment/{id}` | `lookback_days=30`, `min_quality=0.8`, `limit=10000` |

## Tenant SQL

Requires the `sql` extra (`industryflow[sql]`, installed in the authoring image). The proxy assumes
the tenant's read-only role ([ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md)).

```python
from industryflow import IndustryFlowSQL, sql_query

df = sql_query("SELECT * FROM equipment LIMIT 10")

db = IndustryFlowSQL.from_env()
hourly = db.query("SELECT time, value FROM sensor_aggregations_1hour WHERE sensor_id = %s", ["<sensor-id>"])
```

## Cold layer

Requires the `cold` extra (`industryflow[cold]`, installed in the authoring image).

```python
from industryflow import IndustryFlowCold, cold_read_all

history = cold_read_all()                      # every verified Parquet part of the tenant

cold = IndustryFlowCold.from_env()
files = cold.list_files("year=2026")
month = cold.read_all("year=2026/month=07")    # narrow the path to prune partitions
```

## Develop

```bash
pip install -e ".[dev]"
pytest tests/
```
