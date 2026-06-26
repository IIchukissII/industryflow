<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Notebook Utilities

Helper utilities for data scientists working with IndustryFlow ML Service in Jupyter notebooks.

## Installation

These utilities are automatically available in the Jupyter Lab environment. No installation required!

## Usage

### Feature Config Builder

The `FeatureConfigBuilder` helps you create feature engineering configurations programmatically instead of writing JSON manually.

**Quick Start:**

```python
from utils.feature_config_builder import FeatureConfigBuilder

# Create config
config = FeatureConfigBuilder("My Config", "reactor")

# Add sensors
config.add_sensors(["temp_1", "pressure_1", "flow_1"])

# Add transformations
config.add_identity("temp_1")
config.add_polynomial("pressure_1", power=2)
config.add_ratio("pressure_1", "flow_1")

# Register with API
feature_config_id = config.register(
    "http://ml-service-api:8002",
    "your_jwt_token"
)
```

**See full examples in:** `Example_Feature_Config_Builder.ipynb`

## Available Utilities

### FeatureConfigBuilder

Main class for building feature configurations.

**Methods:**
- `add_sensors(sensors)` - Add base sensors
- `add_identity(sensor, name=None)` - Add identity transformation
- `add_polynomial(sensor, power, name=None)` - Add polynomial transformation
- `add_ratio(sensor1, sensor2, name=None)` - Add ratio interaction
- `add_difference(sensor1, sensor2, name=None)` - Add difference interaction
- `add_product(sensor1, sensor2, name=None)` - Add product interaction
- `add_deviation(sensor, baseline, name=None)` - Add statistical deviation
- `add_custom(name, transformation)` - Add custom transformation
- `get_feature_names()` - Get ordered list of feature names
- `to_json(pretty=True)` - Export as JSON string
- `to_dict()` - Export as dictionary
- `save(filepath)` - Save to JSON file
- `register(url, token)` - Register with ML Service API
- `summary()` - Print human-readable summary

### Helper Functions

**create_tep_config(name)**
- Pre-configured builder for TEP process (52 sensors)

**batch_add_identities(config, sensors)**
- Add identity transformations for multiple sensors

**batch_add_polynomials(config, sensors, powers)**
- Add polynomial transformations for multiple sensors and powers

**batch_add_interactions(config, sensor_pairs, operations)**
- Add interaction transformations for multiple sensor pairs

**load_config(filepath)**
- Load configuration from JSON file

## Examples

### Example 1: Simple Configuration

```python
from utils.feature_config_builder import FeatureConfigBuilder

config = FeatureConfigBuilder("Pump Monitoring", "pump")
config.add_sensors(["vibration", "temperature", "pressure"])

# Add features
config.add_identity("vibration")
config.add_polynomial("vibration", power=2)
config.add_ratio("temperature", "pressure")

print(config.summary())
```

### Example 2: Batch Operations

```python
from utils.feature_config_builder import (
    FeatureConfigBuilder,
    batch_add_identities,
    batch_add_polynomials
)

config = FeatureConfigBuilder("Reactor Monitoring", "reactor")

# Add all sensors
sensors = [f"sensor_{i}" for i in range(1, 21)]
config.add_sensors(sensors)

# Batch add identities
batch_add_identities(config, sensors[:10])

# Batch add polynomials
batch_add_polynomials(config, sensors[10:15], powers=[2, 3])

print(f"Total features: {len(config.get_feature_names())}")
```

### Example 3: TEP Process

```python
from utils.feature_config_builder import create_tep_config, batch_add_identities

# Start with TEP template (52 sensors pre-configured)
config = create_tep_config("TEP Anomaly Detection")

# Add important identity features
important = ["xmeas_1", "xmeas_7", "xmeas_8", "xmeas_10"]
batch_add_identities(config, important)

# Add critical interactions
config.add_ratio("xmeas_7", "xmeas_8")  # Reactor pressure/level
config.add_product("xmeas_9", "xmeas_10")  # Temperature * purge rate

# Add deviations for anomaly detection
config.add_deviation("xmeas_7", baseline=2630)  # Reactor pressure baseline

# Register
feature_config_id = config.register(ML_SERVICE_URL, JWT_TOKEN)
```

### Example 4: Complete Training Workflow

```python
import pandas as pd
import xgboost as xgb
import mlflow
from utils.feature_config_builder import FeatureConfigBuilder

# 1. Create feature config
config = FeatureConfigBuilder("Equipment Model v1", "equipment_type")
config.add_sensors(["temp", "pressure", "flow"])
config.add_identity("temp")
config.add_ratio("pressure", "flow")

# 2. Register config
feature_config_id = config.register(ML_SERVICE_URL, JWT_TOKEN)

# 3. Train model
# ... training code ...

# 4. Register model with feature config link
model_data = {
    "model_name": "My Model",
    "model_type": "xgboost",
    "feature_config_id": feature_config_id,  # ← Link!
    "feature_names": config.get_feature_names(),  # ← From config!
    ...
}
```

## Transformation Types

### Identity
Pass-through (no transformation)
```python
config.add_identity("temperature")
# Input: 120.5 → Output: 120.5
```

### Polynomial
Power transformations
```python
config.add_polynomial("pressure", power=2)
# Input: 100 → Output: 10,000

config.add_polynomial("pressure", power=3, name="pressure_cubed")
# Input: 10 → Output: 1,000
```

### Ratio
Division of two sensors
```python
config.add_ratio("pressure", "level")
# Input: pressure=100, level=50 → Output: 2.0
```

### Difference
Subtraction of two sensors
```python
config.add_difference("temp_inlet", "temp_outlet")
# Input: inlet=120, outlet=95 → Output: 25
```

### Product
Multiplication of two sensors
```python
config.add_product("flow", "pressure")
# Input: flow=50, pressure=100 → Output: 5,000
```

### Deviation
Difference from baseline (for anomaly detection)
```python
config.add_deviation("temperature", baseline=120.0)
# Normal: temp=122 → Output: +2
# Anomaly: temp=150 → Output: +30
```

## Best Practices

### Naming Conventions

- Use descriptive names for configurations
- Follow sensor naming standards
- Let auto-naming handle most cases

### Versioning

Use semantic versioning:
- **1.0.0** → Initial version
- **1.1.0** → Added features (backward compatible)
- **2.0.0** → Breaking changes (different sensors, major restructure)

### Feature Engineering Tips

1. **Start Simple:** Begin with identity features
2. **Add Interactions:** Focus on physically meaningful combinations
3. **Use Polynomials:** For non-linear relationships
4. **Add Deviations:** Essential for anomaly detection
5. **Validate:** Check feature distributions before training

### Performance

- Use batch methods for large numbers of features
- Pre-configure common patterns (like TEP template)
- Cache feature configs in production

## Troubleshooting

**Problem: Feature name already exists**
```
ValueError: Feature 'sensor_1' already exists
```
Solution: Use unique names or provide custom names
```python
config.add_identity("sensor_1")  # OK
config.add_identity("sensor_1", name="sensor_1_v2")  # OK - custom name
```

**Problem: Registration fails**
```
requests.exceptions.HTTPError: 401 Unauthorized
```
Solution: Check JWT token validity
```python
# Ensure token is not expired
# Get fresh token from authentication service
```

**Problem: Can't import utilities**
```
ModuleNotFoundError: No module named 'utils'
```
Solution: Ensure correct path
```python
import sys
sys.path.append('/app/notebooks')
from utils.feature_config_builder import FeatureConfigBuilder
```

## Support

For questions or issues:
- Check `Example_Feature_Config_Builder.ipynb` for complete examples
- See the feature engineering & plugin docs: `docs/architecture/ml-and-features.md`, `docs/operations/extensions.md`
- Report bugs: GitHub Issues

## Version History

**1.0.0** (2025-11-22)
- Initial release
- FeatureConfigBuilder class
- Batch helper functions
- TEP process template
- Complete example notebook
