<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Notebook utilities

Python helpers for notebooks: `feature_config_builder` builds and registers feature configurations; `offline_baseline` re-derives the serving window baseline for training data ([ADR-0023](../../../../ADR/ADR-0023-stream-materialized-feature-engineering.md)).

## Setup

The package is not part of a notebook image. Put `services/ml_service/notebooks` on the kernel's
`sys.path` and install the shared contracts package, which `offline_baseline` reads window widths and
lateness from ([ADR-0006](../../../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) dec 7):

```bash
pip install <repo>/libs/industryflow_contracts
```

```python
import sys
sys.path.append("<repo>/services/ml_service/notebooks")
from utils import FeatureConfigBuilder, add_window_features
```

## `FeatureConfigBuilder`

`FeatureConfigBuilder(name, equipment_type, description=None, version="1.0.0")`. Every `add_*`
method returns the builder and raises `ValueError` if the feature name already exists.

| Method | Transformation emitted | Default feature name |
|---|---|---|
| `add_sensors(sensors)` | appends to `base_sensors` | |
| `add_identity(sensor, name=None)` | `identity` | `<sensor>` |
| `add_polynomial(sensor, power, name=None)` | `polynomial`, `params.power` | `<sensor>_squared`, `<sensor>_cubed`, `<sensor>_power<n>` |
| `add_ratio(sensor1, sensor2, name=None)` | `interaction`, `operation: ratio` | `<sensor1>_<sensor2>_ratio` |
| `add_difference(sensor1, sensor2, name=None)` | `interaction`, `operation: difference` | `<sensor1>_<sensor2>_diff` |
| `add_product(sensor1, sensor2, name=None)` | `interaction`, `operation: product` | `<sensor1>_<sensor2>_product` |
| `add_deviation(sensor, granularity="1min", name=None)` | `statistical`, `stat_type: deviation_from_window_mean` | `<sensor>_deviation` |
| `add_custom(feature_name, transformation)` | the given dict, with `name` set | `feature_name` |

| Method | Returns |
|---|---|
| `get_feature_names()` | Feature names in transformation order |
| `to_json(pretty=True)` | JSON string |
| `to_dict()` | Shallow copy of the config dict |
| `save(filepath)` | Writes JSON to `filepath` |
| `register(ml_service_url, jwt_token)` | `POST <ml_service_url>/api/feature-configs` with a bearer token; returns the config `id`, raises `requests.HTTPError` on a non-201 response |
| `summary()` | Summary string (type counts, first five features) |

### Module functions

| Function | Effect |
|---|---|
| `load_config(filepath)` | `FeatureConfigBuilder` from a JSON file, existing feature names tracked |
| `batch_add_identities(config, sensors)` | `add_identity` per sensor |
| `batch_add_polynomials(config, sensors, powers)` | `add_polynomial` per sensor and power |
| `batch_add_interactions(config, sensor_pairs, operations=None)` | `add_ratio` / `add_difference` / `add_product` per pair; default all three |

## `offline_baseline`

A `statistical` feature at serve time reads the newest closed window of `sensor_aggregations_<granularity>`.
`offline_baseline` computes the same value per row from history, causally, so training and serving
agree. Granularities and their width/lateness come from `industryflow_contracts.windows`
(`1min`, `5min`, `1hour`; default `1min`).

| Name | Signature / value | Returns |
|---|---|---|
| `add_window_features` | `(df, sensor_cols, *, granularity="1min", stats=("deviation_from_window_mean",), timestamp_col="timestamp", group_cols=())` | `(df, transformations)`: one column per (sensor, stat) plus the matching config entries; row order preserved |
| `feature_config` | `(sensor, stat_type, granularity="1min", name=None)` | One `statistical` transformation dict |
| `window_index` | `(timestamps, granularity="1min")` | Tumbling-window index per timestamp |
| `latest_written_window` | `(timestamps, granularity="1min")` | Index of the newest window the Spark job has written at each timestamp |
| `GRANULARITY_SECONDS` | `dict[str, int]` | Window width per granularity |
| `WATERMARK_SECONDS` | `dict[str, int]` | Allowed lateness per granularity |

| `stat_type` | Column suffix | Value |
|---|---|---|
| `deviation_from_window_mean` | `_deviation` | reading minus the window mean |
| `window_mean` | `_window_mean` | window mean |
| `window_std` | `_window_std` | window standard deviation |

Rows with no written window yet get `0.0`, the serving fallback. A frame holding more than one
equipment's history needs `group_cols=["equipment_id"]` (or its equivalent).

## Example

```python
from utils import FeatureConfigBuilder, add_window_features

df, window_transforms = add_window_features(df, ["temp", "pressure"], granularity="1min")

config = FeatureConfigBuilder("Pump model v1", "pump")
config.add_sensors(["temp", "pressure", "flow"])
config.add_identity("temp")
config.add_ratio("pressure", "flow")
for t in window_transforms:
    config.add_custom(t["name"], t)

feature_config_id = config.register("<ml-service-url>", "<jwt>")
feature_names = config.get_feature_names()
```

A domain's sensor set lives in its extension ([ADR-0008](../../../../ADR/ADR-0008-extension-and-plugin-interface.md));
`load_config("extensions/tep-reference/tep_reactor_config.json")` starts from the TEP example.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ValueError: Feature '<name>' already exists` | Name taken, also by a feature in a loaded config | Pass `name=` |
| `requests.exceptions.HTTPError: 401` from `register` | Token missing or expired | Use a current access token |
| `ModuleNotFoundError: No module named 'utils'` | Directory not on `sys.path` | See [Setup](#setup) |
| `ModuleNotFoundError: No module named 'industryflow_contracts'` | Contracts package not installed | `pip install <repo>/libs/industryflow_contracts` |

## Related

| Page | Topic |
|---|---|
| [ML and features](../../../../docs/architecture/ml-and-features.md) | Feature engineering, transformation types |
| [extensions](../../../../docs/operations/extensions.md) | Domain extensions |
