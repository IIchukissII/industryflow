<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Python notebook client

The `industryflow` package: tenant data from a notebook kernel as pandas DataFrames
([ADR-0011](../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) dec 4). The
kernel holds only capability handles minted by the notebook hub; tenant scoping is enforced
server-side ([ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)). Kernel variables and
profiles: [notebooks](../../docs/architecture/notebooks.md#kernel-environment). Tests:
[getting started](../../docs/getting-started.md#tests).

## Exports

| Name | Purpose | Backend | Profiles |
|---|---|---|---|
| `IndustryFlowClient` | Measurements, aggregations, training data | API gateway data API, handle in `X-IF-Capability` | authoring, analytics |
| `IndustryFlowSQL`, `sql_query` | Read-only tenant SQL | SQL proxy, handle as the Postgres password | authoring |
| `IndustryFlowCold`, `cold_read_all` | Cold-layer Parquet history | Cold-store broker, pre-signed object-store reads ([ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md)) | authoring |
| `SKOPS_TRUSTED_TYPES` | Types to pass to `mlflow.sklearn.log_model` for tree-based models ([ADR-0027](../../ADR/ADR-0027-model-artifact-supply-chain-parity.md)) | — | authoring |

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
|---|---|---|
| `measurements(...)` | `GET /api/measurements` | `order="asc"`, `limit=1000` |
| `aggregations(window, ...)` | `GET /api/aggregations/{window}`; `window` is `1min`, `5min` or `1hour` | `order="asc"`, `limit=1000` |
| `training_data(equipment_id, ...)` | `GET /api/training-data/equipment/{id}` | `lookback_days=30`, `min_quality=0.8`, `limit=10000` |

## Tenant SQL

Requires the `sql` extra (`industryflow[sql]`, installed in the authoring image). The proxy assumes
the per-tenant read-only role ([ADR-0015](../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md)).

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
