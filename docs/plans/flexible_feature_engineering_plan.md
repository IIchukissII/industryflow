# Flexible Feature Engineering System - Implementation Plan

**Date**: 2025-01-21
**Version**: 1.0
**Status**: Draft

---

## 1. Executive Summary

### Problem Statement
The current feature engineering implementation is hardcoded for TEP (Tennessee Eastman Process) equipment and cannot adapt to different equipment types (motors, pumps, turbines, etc.). Each equipment type requires different sensors and different feature engineering transformations.

### Current Limitations
1. **Hardcoded TEP features**: Feature names and transformations specific to TEP reactor
2. **No historical data**: Cannot compute rolling statistics (model needs run_mean, run_std)
3. **Single equipment support**: Cannot handle multiple equipment types
4. **Individual sensor readings**: Stream sends one sensor at a time, but model needs all 52 sensors + engineered features
5. **Inflexible**: New equipment requires code changes instead of configuration

### Proposed Solution
Build a **configuration-driven, generic feature engineering system** with:
- Feature Store for recent sensor readings
- Feature Engineering Registry for equipment-specific rules
- Generic transformation engine
- Per-equipment-type feature configurations

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                     SENSOR DATA INGESTION                            │
│  stream_tep_data.py → Ingestion Service → Kafka → Alert Detector    │
└──────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────┐
│                    COMPONENT 1: FEATURE STORE                        │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ Redis / In-Memory Cache                                        │ │
│  │ Key: equipment_id:sensor_name                                  │ │
│  │ Value: [{timestamp, value}, {timestamp, value}, ...]           │ │
│  │ Window: Last 100 readings per sensor (configurable)            │ │
│  │ TTL: 1 hour                                                    │ │
│  └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────┐
│              COMPONENT 2: FEATURE ENGINEERING REGISTRY               │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ Database Table: feature_engineering_configs                    │ │
│  │                                                                │ │
│  │ Columns:                                                       │ │
│  │  - id (UUID)                                                   │ │
│  │  - equipment_type (string): "tep_reactor", "motor", "pump"     │ │
│  │  - base_sensors (jsonb): ["xmeas_1", "xmeas_2", ...]          │ │
│  │  - transformations (jsonb): [                                  │ │
│  │      {type: "polynomial", sensor: "xmeas_7", power: 2},       │ │
│  │      {type: "interaction", sensors: ["xmeas_7", "xmeas_8"],   │ │
│  │       operation: "ratio"},                                     │ │
│  │      {type: "rolling_stat", sensor: "xmeas_18",               │ │
│  │       window: 10, stat: "mean"}                                │ │
│  │    ]                                                           │ │
│  │  - created_at, updated_at                                      │ │
│  └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────┐
│            COMPONENT 3: FEATURE ENGINEERING ENGINE                   │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ Generic Transformation Functions:                              │ │
│  │                                                                │ │
│  │ 1. identity(sensor_value) → value                             │ │
│  │ 2. polynomial(sensor_value, power) → value^power              │ │
│  │ 3. interaction(sensor1, sensor2, op) → sensor1 op sensor2     │ │
│  │    - Operations: ratio, diff, product, sum                    │ │
│  │ 4. rolling_stat(sensor_history, window, stat)                 │ │
│  │    - Stats: mean, std, min, max, median, quantile             │ │
│  │ 5. cross_sensor_stat(sensors, stat) → stat across sensors     │ │
│  │ 6. lag(sensor_history, n) → value from n steps ago            │ │
│  │ 7. delta(sensor_history, n) → current - value_n_steps_ago     │ │
│  │ 8. deviation(sensor_value, rolling_mean) → value - mean       │ │
│  └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────┐
│              COMPONENT 4: MODEL METADATA UPDATES                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ ml_models table - New columns:                                 │ │
│  │  - equipment_type (string): "tep_reactor"                      │ │
│  │  - feature_config_id (UUID): FK to feature_engineering_configs │ │
│  │  - feature_names (jsonb): final selected features              │ │
│  │                           (after feature selection during      │ │
│  │                            training)                            │ │
│  └────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Component Design

### 3.1 Feature Store

**Purpose**: Store recent sensor readings to enable windowed aggregations and historical features

**Technology Options**:
1. **Redis** (preferred for production)
   - Fast in-memory storage
   - Built-in TTL expiration
   - Supports lists/time-series data structures

2. **In-memory Python dict** (development/testing)
   - Simple implementation
   - No external dependencies
   - Limited by memory

**Data Structure**:
```python
# Redis key-value structure
Key: "features:{equipment_id}:{sensor_name}"
Value: [
    {"timestamp": "2025-01-21T10:00:00Z", "value": 123.45},
    {"timestamp": "2025-01-21T10:00:01Z", "value": 123.50},
    ...
]
# Keep last 100 readings (configurable)
# TTL: 3600 seconds (1 hour)
```

**API**:
```python
class FeatureStore:
    async def store_reading(
        self,
        equipment_id: str,
        sensor_name: str,
        timestamp: str,
        value: float
    ) -> None

    async def get_recent_readings(
        self,
        equipment_id: str,
        sensor_name: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]

    async def get_current_snapshot(
        self,
        equipment_id: str,
        sensor_names: List[str]
    ) -> Dict[str, float]
```

**Integration Points**:
- Alert Detector worker updates Feature Store on each sensor reading
- ML Inference endpoint reads from Feature Store to get current snapshot + history

---

### 3.2 Feature Engineering Registry

**Purpose**: Store equipment-specific feature engineering configurations

**Database Schema**:
```sql
CREATE TABLE feature_engineering_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id UUID NOT NULL,
    equipment_type VARCHAR(100) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,

    -- List of base sensor names required
    base_sensors JSONB NOT NULL,
    -- Example: ["xmeas_1", "xmeas_2", ..., "xmv_11"]

    -- List of transformation definitions
    transformations JSONB NOT NULL,
    -- Example: see transformation schema below

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(company_id, equipment_type, name)
);

CREATE INDEX idx_feature_configs_equipment_type
ON feature_engineering_configs(company_id, equipment_type);
```

**Transformation Schema** (JSONB):
```json
{
  "transformations": [
    {
      "name": "xmeas_7_squared",
      "type": "polynomial",
      "sensor": "xmeas_7",
      "params": {"power": 2}
    },
    {
      "name": "xmeas_7_xmeas_8_ratio",
      "type": "interaction",
      "sensors": ["xmeas_7", "xmeas_8"],
      "params": {"operation": "ratio"}
    },
    {
      "name": "xmeas_18_rolling_mean",
      "type": "rolling_stat",
      "sensor": "xmeas_18",
      "params": {"window": 10, "stat": "mean"}
    },
    {
      "name": "xmeas_7_deviation",
      "type": "deviation",
      "sensor": "xmeas_7",
      "params": {"reference": "xmeas_7_run_mean"}
    },
    {
      "name": "sensors_mean",
      "type": "cross_sensor_stat",
      "sensors": "all",
      "params": {"stat": "mean"}
    }
  ]
}
```

**Supported Transformation Types**:

| Type | Description | Params | Example |
|------|-------------|--------|---------|
| `identity` | Raw sensor value | - | `xmeas_1` |
| `polynomial` | Raise to power | `power` (int) | `xmeas_7^2` |
| `interaction` | Binary operation | `operation`: ratio/diff/product/sum | `xmeas_7 / xmeas_8` |
| `rolling_stat` | Windowed statistic | `window` (int), `stat`: mean/std/min/max | `mean(last 10 readings)` |
| `cross_sensor_stat` | Stat across sensors | `stat`: mean/std/min/max/range | `mean(all sensors)` |
| `lag` | Previous value | `n` (steps back) | `value from 5 steps ago` |
| `delta` | Change from past | `n` (steps back) | `current - value_n_ago` |
| `deviation` | Diff from reference | `reference` (feature name) | `value - rolling_mean` |

---

### 3.3 Feature Engineering Engine

**Purpose**: Apply transformations dynamically based on configuration

**Implementation**:
```python
# File: services/ml_service/api/feature_engineering/engine.py

from typing import Dict, List, Any
import numpy as np

class FeatureEngineeringEngine:
    """
    Generic feature engineering engine
    Applies transformations based on configuration
    """

    def __init__(self, feature_store):
        self.feature_store = feature_store
        self.transformations = {
            'identity': self._transform_identity,
            'polynomial': self._transform_polynomial,
            'interaction': self._transform_interaction,
            'rolling_stat': self._transform_rolling_stat,
            'cross_sensor_stat': self._transform_cross_sensor_stat,
            'lag': self._transform_lag,
            'delta': self._transform_delta,
            'deviation': self._transform_deviation,
        }

    async def engineer_features(
        self,
        equipment_id: str,
        config: Dict[str, Any],
        feature_names: List[str] = None
    ) -> np.ndarray:
        """
        Engineer features for an equipment based on config

        Args:
            equipment_id: Equipment UUID
            config: Feature engineering config from registry
            feature_names: Optional list of features to extract (after selection)

        Returns:
            np.ndarray: Engineered features (1, n_features)
        """
        # 1. Get current sensor snapshot from feature store
        base_sensors = config['base_sensors']
        snapshot = await self.feature_store.get_current_snapshot(
            equipment_id, base_sensors
        )

        # 2. Get historical data for rolling features
        history = {}
        for sensor in base_sensors:
            history[sensor] = await self.feature_store.get_recent_readings(
                equipment_id, sensor, limit=100
            )

        # 3. Apply all transformations
        engineered_features = {}

        for transform_def in config['transformations']:
            feature_name = transform_def['name']
            transform_type = transform_def['type']
            params = transform_def.get('params', {})

            # Get transformation function
            transform_fn = self.transformations.get(transform_type)
            if not transform_fn:
                raise ValueError(f"Unknown transformation type: {transform_type}")

            # Apply transformation
            value = transform_fn(
                transform_def=transform_def,
                snapshot=snapshot,
                history=history,
                engineered_features=engineered_features  # for references
            )

            engineered_features[feature_name] = value

        # 4. Select final features if feature_names specified
        if feature_names:
            selected = [engineered_features.get(name, 0.0) for name in feature_names]
        else:
            selected = list(engineered_features.values())

        # 5. Return as numpy array
        return np.array([selected], dtype=np.float32)

    def _transform_identity(self, transform_def, snapshot, **kwargs):
        sensor = transform_def['sensor']
        return snapshot.get(sensor, 0.0)

    def _transform_polynomial(self, transform_def, snapshot, **kwargs):
        sensor = transform_def['sensor']
        power = transform_def['params']['power']
        value = snapshot.get(sensor, 0.0)
        return value ** power

    def _transform_interaction(self, transform_def, snapshot, **kwargs):
        sensors = transform_def['sensors']
        operation = transform_def['params']['operation']

        val1 = snapshot.get(sensors[0], 0.0)
        val2 = snapshot.get(sensors[1], 0.0)

        if operation == 'ratio':
            return val1 / (val2 + 1e-8)
        elif operation == 'diff':
            return val1 - val2
        elif operation == 'product':
            return val1 * val2
        elif operation == 'sum':
            return val1 + val2
        else:
            raise ValueError(f"Unknown operation: {operation}")

    def _transform_rolling_stat(self, transform_def, history, **kwargs):
        sensor = transform_def['sensor']
        window = transform_def['params']['window']
        stat = transform_def['params']['stat']

        readings = history.get(sensor, [])
        if len(readings) < window:
            return 0.0  # Not enough data

        values = [r['value'] for r in readings[-window:]]

        if stat == 'mean':
            return np.mean(values)
        elif stat == 'std':
            return np.std(values)
        elif stat == 'min':
            return np.min(values)
        elif stat == 'max':
            return np.max(values)
        elif stat == 'median':
            return np.median(values)
        else:
            raise ValueError(f"Unknown stat: {stat}")

    def _transform_cross_sensor_stat(self, transform_def, snapshot, **kwargs):
        sensors = transform_def['sensors']
        stat = transform_def['params']['stat']

        if sensors == 'all':
            values = list(snapshot.values())
        else:
            values = [snapshot.get(s, 0.0) for s in sensors]

        if stat == 'mean':
            return np.mean(values)
        elif stat == 'std':
            return np.std(values)
        elif stat == 'min':
            return np.min(values)
        elif stat == 'max':
            return np.max(values)
        elif stat == 'range':
            return np.max(values) - np.min(values)
        else:
            raise ValueError(f"Unknown stat: {stat}")

    def _transform_lag(self, transform_def, history, **kwargs):
        sensor = transform_def['sensor']
        n = transform_def['params']['n']

        readings = history.get(sensor, [])
        if len(readings) < n:
            return 0.0

        return readings[-n]['value']

    def _transform_delta(self, transform_def, snapshot, history, **kwargs):
        sensor = transform_def['sensor']
        n = transform_def['params']['n']

        current = snapshot.get(sensor, 0.0)
        readings = history.get(sensor, [])

        if len(readings) < n:
            return 0.0

        past = readings[-n]['value']
        return current - past

    def _transform_deviation(self, transform_def, snapshot, engineered_features, **kwargs):
        sensor = transform_def['sensor']
        reference = transform_def['params']['reference']

        current = snapshot.get(sensor, 0.0)
        reference_value = engineered_features.get(reference, 0.0)

        return current - reference_value
```

---

### 3.4 Model Metadata Updates

**Database Migration**:
```sql
-- Add new columns to ml_models table
ALTER TABLE ml_models
ADD COLUMN equipment_type VARCHAR(100),
ADD COLUMN feature_config_id UUID REFERENCES feature_engineering_configs(id);

-- Update existing TEP model
UPDATE ml_models
SET equipment_type = 'tep_reactor'
WHERE model_id = '84f6f58b-7296-4a56-abb3-7e71a4d0bcf2';
```

**Model Registration Flow**:
1. Train model with specific feature engineering config
2. Perform feature selection (126 → 64 features)
3. Register model with:
   - `equipment_type`: "tep_reactor"
   - `feature_config_id`: UUID of config used
   - `feature_names`: List of 64 selected features
4. During inference:
   - Load model metadata
   - Get feature_config from registry
   - Apply feature engineering
   - Select final features using feature_names
   - Run prediction

---

## 4. Implementation Phases

### Phase 1: Feature Store (Week 1)
**Goal**: Store and retrieve recent sensor readings

**Tasks**:
- [ ] Create `FeatureStore` class with Redis backend
- [ ] Implement `store_reading()`, `get_recent_readings()`, `get_current_snapshot()`
- [ ] Add Redis to docker-compose.yml
- [ ] Update Alert Detector to write to Feature Store
- [ ] Write unit tests
- [ ] Test with streaming TEP data

**Files to Create**:
- `services/ml_service/api/feature_engineering/feature_store.py`
- `services/ml_service/api/feature_engineering/__init__.py`

**Files to Modify**:
- `services/alert_service/worker/rules_engine.py` (add Feature Store writes)
- `docker-compose.yml` (add Redis service)

**Acceptance Criteria**:
- Redis container runs successfully
- Sensor readings stored with TTL
- Can retrieve last N readings per sensor
- Can get current snapshot of all sensors for equipment

---

### Phase 2: Feature Engineering Registry (Week 2)
**Goal**: Store feature engineering configurations in database

**Tasks**:
- [ ] Create database migration for `feature_engineering_configs` table
- [ ] Create repository methods (create, get, list configs)
- [ ] Create API endpoints for managing configs
- [ ] Create TEP config JSON with 126 features
- [ ] Write unit tests
- [ ] Load TEP config into database

**Files to Create**:
- `services/ml_service/migrations/XXXX_create_feature_configs.sql`
- `services/ml_service/api/feature_engineering/registry.py`
- `services/ml_service/api/routers/feature_configs.py`
- `services/ml_service/configs/tep_reactor_features.json`

**Files to Modify**:
- `services/ml_service/api/repository.py` (add feature config methods)
- `services/ml_service/api/main.py` (register feature config router)

**Acceptance Criteria**:
- Database table created
- Can CRUD feature configs via API
- TEP config loaded with all 126 transformations
- Config validates transformation types

---

### Phase 3: Feature Engineering Engine (Week 3)
**Goal**: Generic transformation engine

**Tasks**:
- [ ] Create `FeatureEngineeringEngine` class
- [ ] Implement all transformation functions (8 types)
- [ ] Integrate with Feature Store
- [ ] Write comprehensive unit tests for each transformation
- [ ] Test with TEP data and config
- [ ] Verify output matches hardcoded version

**Files to Create**:
- `services/ml_service/api/feature_engineering/engine.py`
- `services/ml_service/api/feature_engineering/transformations.py`

**Files to Modify**:
- None (new component)

**Acceptance Criteria**:
- All 8 transformation types implemented
- Engine generates 126 features from TEP config
- Output matches previous hardcoded implementation
- Unit test coverage > 90%

---

### Phase 4: Integration & Migration (Week 4)
**Goal**: Replace hardcoded feature engineering with generic engine

**Tasks**:
- [ ] Migrate ml_models table (add equipment_type, feature_config_id)
- [ ] Update model registration to save equipment_type and feature_config_id
- [ ] Update inference endpoint to use FeatureEngineeringEngine
- [ ] Test end-to-end: streaming → Feature Store → Inference → Prediction
- [ ] Update existing TEP model metadata
- [ ] Remove old hardcoded feature_engineering.py
- [ ] Update documentation

**Files to Create**:
- `services/ml_service/migrations/XXXX_add_equipment_type_to_models.sql`

**Files to Modify**:
- `services/ml_service/api/routers/models.py` (model registration)
- `services/ml_service/api/routers/inference.py` (use engine)
- `services/ml_service/api/repository.py` (update queries)

**Files to Delete**:
- `services/ml_service/api/feature_engineering.py` (old hardcoded version)

**Acceptance Criteria**:
- Existing TEP model works with new system
- Inference predictions match previous results
- No hardcoded feature engineering remains
- End-to-end test passes

---

### Phase 5: Multi-Equipment Support (Week 5)
**Goal**: Add support for different equipment types

**Tasks**:
- [ ] Create feature config for "motor" equipment type
- [ ] Create feature config for "pump" equipment type
- [ ] Train sample models for new equipment types
- [ ] Test inference with multiple equipment types
- [ ] Create admin UI for managing feature configs
- [ ] Write user documentation

**Files to Create**:
- `services/ml_service/configs/motor_features.json`
- `services/ml_service/configs/pump_features.json`

**Acceptance Criteria**:
- 3+ equipment types supported (TEP, motor, pump)
- Each equipment type has unique feature engineering
- Models for different equipment types can coexist
- Configuration can be managed without code changes

---

## 5. Data Flow Example

### Scenario: TEP Anomaly Detection

**Step 1: Sensor Data Streaming**
```
stream_tep_data.py sends:
{
  "equipment_id": "550e8400-e29b-41d4-a716-446655440100",
  "sensor_id": "sensor-xmeas-7",
  "value": 50.2,
  "timestamp": "2025-01-21T10:00:00Z"
}
```

**Step 2: Alert Detector + Feature Store**
```python
# In rules_engine.py
await feature_store.store_reading(
    equipment_id="550e8400-e29b-41d4-a716-446655440100",
    sensor_name="xmeas_7",
    timestamp="2025-01-21T10:00:00Z",
    value=50.2
)
```

**Step 3: ML Alert Triggered**
```python
# rules_engine.py detects ML rule
ml_rule = {
    "type": "ml",
    "model_id": "84f6f58b-7296-4a56-abb3-7e71a4d0bcf2",
    "threshold": 0.85
}
```

**Step 4: ML Inference with Feature Engineering**
```python
# inference.py

# 1. Load model metadata
model_data = await repository.get_model_by_id(model_id)
equipment_type = model_data['equipment_type']  # "tep_reactor"
feature_config_id = model_data['feature_config_id']
feature_names = model_data['feature_names']  # 64 selected features

# 2. Load feature engineering config
config = await feature_registry.get_config(feature_config_id)

# 3. Engineer features
engine = FeatureEngineeringEngine(feature_store)
features = await engine.engineer_features(
    equipment_id=equipment_id,
    config=config,
    feature_names=feature_names
)
# Result: (1, 64) numpy array

# 4. Run prediction
model = load_model_from_mlflow(mlflow_run_id)
prediction = model.predict(features)

# 5. Return anomaly score
return {
    "anomaly_score": 0.92,
    "is_anomaly": True,
    "threshold": 0.85
}
```

---

## 6. Testing Strategy

### Unit Tests
- Each transformation function independently
- Feature Store CRUD operations
- Feature Registry CRUD operations
- Engine integration with mocked Feature Store

### Integration Tests
- End-to-end: Stream data → Feature Store → Engine → Prediction
- Multiple equipment types simultaneously
- Feature config validation
- Model registration with feature configs

### Performance Tests
- Feature Store latency (target: < 10ms per lookup)
- Engine feature engineering time (target: < 100ms for 126 features)
- Inference throughput (target: > 100 predictions/sec)

### Regression Tests
- Verify new system produces same results as hardcoded TEP features
- Compare predictions before/after migration

---

## 7. Configuration Examples

### Example 1: TEP Reactor Full Config
```json
{
  "equipment_type": "tep_reactor",
  "name": "TEP_Balanced_50_50_Features",
  "description": "Feature engineering for TEP binary anomaly detection (balanced training)",
  "base_sensors": [
    "xmeas_1", "xmeas_2", "xmeas_3", "xmeas_4", "xmeas_5", "xmeas_6",
    "xmeas_7", "xmeas_8", "xmeas_9", "xmeas_10", "xmeas_11", "xmeas_12",
    "xmeas_13", "xmeas_14", "xmeas_15", "xmeas_16", "xmeas_17", "xmeas_18",
    "xmeas_19", "xmeas_20", "xmeas_21", "xmeas_22", "xmeas_23", "xmeas_24",
    "xmeas_25", "xmeas_26", "xmeas_27", "xmeas_28", "xmeas_29", "xmeas_30",
    "xmeas_31", "xmeas_32", "xmeas_33", "xmeas_34", "xmeas_35", "xmeas_36",
    "xmeas_37", "xmeas_38", "xmeas_39", "xmeas_40", "xmeas_41",
    "xmv_1", "xmv_2", "xmv_3", "xmv_4", "xmv_5", "xmv_6", "xmv_7",
    "xmv_8", "xmv_9", "xmv_10", "xmv_11"
  ],
  "transformations": [
    {"name": "xmeas_1", "type": "identity", "sensor": "xmeas_1"},
    {"name": "xmeas_2", "type": "identity", "sensor": "xmeas_2"},

    {"name": "xmeas_1_xmeas_2_ratio", "type": "interaction",
     "sensors": ["xmeas_1", "xmeas_2"], "params": {"operation": "ratio"}},
    {"name": "xmeas_1_xmeas_2_diff", "type": "interaction",
     "sensors": ["xmeas_1", "xmeas_2"], "params": {"operation": "diff"}},

    {"name": "xmeas_7_squared", "type": "polynomial",
     "sensor": "xmeas_7", "params": {"power": 2}},
    {"name": "xmeas_7_cubed", "type": "polynomial",
     "sensor": "xmeas_7", "params": {"power": 3}},

    {"name": "xmeas_7_rolling_mean", "type": "rolling_stat",
     "sensor": "xmeas_7", "params": {"window": 10, "stat": "mean"}},
    {"name": "xmeas_7_deviation", "type": "deviation",
     "sensor": "xmeas_7", "params": {"reference": "xmeas_7_rolling_mean"}},

    {"name": "sensors_mean", "type": "cross_sensor_stat",
     "sensors": "all", "params": {"stat": "mean"}},
    {"name": "sensors_std", "type": "cross_sensor_stat",
     "sensors": "all", "params": {"stat": "std"}},
    {"name": "sensors_range", "type": "cross_sensor_stat",
     "sensors": "all", "params": {"stat": "range"}}
  ]
}
```

### Example 2: Motor Config
```json
{
  "equipment_type": "motor",
  "name": "Motor_Vibration_Features",
  "description": "Feature engineering for electric motor vibration analysis",
  "base_sensors": [
    "vibration_x", "vibration_y", "vibration_z",
    "temperature", "current", "voltage", "speed_rpm"
  ],
  "transformations": [
    {"name": "vibration_x", "type": "identity", "sensor": "vibration_x"},
    {"name": "vibration_magnitude", "type": "cross_sensor_stat",
     "sensors": ["vibration_x", "vibration_y", "vibration_z"],
     "params": {"stat": "mean"}},
    {"name": "vibration_x_rolling_rms", "type": "rolling_stat",
     "sensor": "vibration_x", "params": {"window": 20, "stat": "std"}},
    {"name": "temperature_delta", "type": "delta",
     "sensor": "temperature", "params": {"n": 5}},
    {"name": "power", "type": "interaction",
     "sensors": ["current", "voltage"], "params": {"operation": "product"}}
  ]
}
```

---

## 8. Risks & Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Redis failure causes inference to fail | High | Low | Implement graceful degradation (use defaults if Feature Store unavailable) |
| Feature Store memory usage grows unbounded | Medium | Medium | Implement TTL + max window size limits |
| Performance degradation with many transformations | Medium | Medium | Cache engineered features for short period (30s), benchmark early |
| Complex feature configs difficult to debug | Low | High | Add validation, logging, and feature preview API |
| Migration breaks existing TEP model | High | Low | Comprehensive regression tests before migration |

---

## 9. Success Metrics

### Technical Metrics
- Feature engineering time: < 100ms for 126 features
- Feature Store lookup: < 10ms
- Inference throughput: > 100 predictions/second
- System uptime: > 99.9%

### Functional Metrics
- Support 5+ equipment types without code changes
- Feature configs can be created/updated via API
- Existing TEP model predictions match previous system (< 1% deviation)
- New equipment type can be onboarded in < 1 day

---

## 10. Future Enhancements

### Phase 6+ (Future)
- **Feature Store Backend Options**: Support PostgreSQL TimescaleDB for Feature Store
- **Advanced Transformations**: FFT, wavelet transforms for vibration analysis
- **Automatic Feature Engineering**: AutoML-style feature discovery
- **Feature Importance Tracking**: Monitor which features contribute most to predictions
- **Feature Drift Detection**: Alert when feature distributions change
- **Multi-Equipment Correlation**: Features across related equipment (e.g., pump + motor)
- **Streaming Feature Engineering**: Real-time feature computation in Kafka Streams

---

## 11. Timeline Summary

| Phase | Duration | Dependencies | Deliverables |
|-------|----------|--------------|--------------|
| Phase 1: Feature Store | 1 week | Redis, Alert Service | Working Feature Store with Redis backend |
| Phase 2: Registry | 1 week | PostgreSQL | Database schema + API endpoints for configs |
| Phase 3: Engine | 1 week | Phase 1 | Generic transformation engine with 8 types |
| Phase 4: Integration | 1 week | Phase 1-3 | Migrated system, old code removed |
| Phase 5: Multi-Equipment | 1 week | Phase 4 | 3+ equipment types supported |

**Total: 5 weeks**

---

## 12. Appendix

### A. Feature Engineering Comparison

| Aspect | Old (Hardcoded) | New (Flexible) |
|--------|----------------|----------------|
| Equipment support | TEP only | Any equipment type |
| Feature definition | Python code | JSON configuration |
| Adding new equipment | Code changes + deployment | Config upload via API |
| Historical features | Not supported (fake with zeros) | Full support via Feature Store |
| Maintainability | Low (hardcoded lists) | High (configuration-driven) |
| Testability | Hard to test all scenarios | Easy to test with mock configs |
| Scalability | One-off per equipment | Scales to unlimited equipment |

### B. Database Tables Summary

```sql
-- New table
feature_engineering_configs (
    id, company_id, equipment_type, name,
    base_sensors JSONB, transformations JSONB,
    created_at, updated_at
)

-- Modified table
ml_models (
    ..existing columns..,
    equipment_type VARCHAR(100),
    feature_config_id UUID REFERENCES feature_engineering_configs(id)
)
```

### C. API Endpoints

```
POST   /api/feature-configs              Create feature config
GET    /api/feature-configs              List all configs
GET    /api/feature-configs/{id}         Get config by ID
GET    /api/feature-configs/equipment/{type}  Get configs for equipment type
PUT    /api/feature-configs/{id}         Update config
DELETE /api/feature-configs/{id}         Delete config

GET    /api/feature-configs/{id}/preview Preview features with test data
```

---

**End of Plan**

*This plan will be reviewed and updated as implementation progresses.*
