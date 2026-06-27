<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# industryflow (Python notebook client)

The blessed, tenant-scoped way to load a tenant's data into a notebook as pandas DataFrames
(ADR-0011 dec 4). It calls the platform's existing read API over HTTP, authenticated **as the
user** with a per-session capability minted by the notebook spawner (ADR-0012). It never holds
a database or object-store credential.

> **Status: phase-1 skeleton.** The capability is carried as a bearer token; the spawner that
> mints and injects it lands in a later phase. The API surface it targets is live today.

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

## Develop

```bash
pip install -e ".[dev]"
pytest tests/
```
