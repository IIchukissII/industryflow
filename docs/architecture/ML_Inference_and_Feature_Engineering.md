<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ML Inference and Feature Engineering Architecture

**Component:** ML Anomaly Detection System
**Services:** ML Service, Alert Service, Feature Store
**Version:** 2.0.0
**Date:** November 2025

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Components](#2-architecture-components)
3. [ML Inference Pipeline](#3-ml-inference-pipeline)
4. [Feature Engineering System](#4-feature-engineering-system)
5. [Feature Store](#5-feature-store)
6. [Alert Service Integration](#6-alert-service-integration)
7. [Implementation Details](#7-implementation-details)
8. [Troubleshooting Guide](#8-troubleshooting-guide)

---

## 1. System Overview

### 1.1 Purpose

The ML Anomaly Detection System provides real-time equipment-level anomaly detection using machine learning models. It combines multi-sensor data, configuration-driven feature engineering, and MLflow-managed models to detect complex anomalies that simple threshold rules cannot catch.

### 1.2 Key Features

- **Equipment-Level Detection**: Evaluates multiple sensors together for multivariate anomaly detection
- **Configuration-Driven**: Feature engineering transformations defined in database, not hardcoded
- **Feature Store**: Redis-based time-series storage for rolling statistics
- **MLflow Integration**: Model versioning, experiment tracking, and centralized storage
- **Real-Time Inference**: 10-second evaluation cycle with <200ms latency
- **Anomaly Scoring**: Probability-based scores (0.0-1.0) for alert prioritization

### 1.3 Data Flow

```
Sensor Readings (Kafka)
        │
        ▼
┌───────────────────────┐
│   Alert Service       │
│   - Store in Feature  │──────┐
│     Store (Redis)     │      │
└───────────────────────┘      │
        │                       │
        │ Every 10s             │ Historical Data
        ▼                       ▼
┌──────────────────────────────────────┐
│   Equipment-Level ML Evaluation      │
│   1. Get Feature Config              │
│   2. Query Feature Store Snapshot    │
│   3. Build sensor_data dict          │
└──────────┬───────────────────────────┘
           │
           │ HTTP POST
           ▼
┌──────────────────────────────────────┐
│         ML Service                   │
│   /api/inference/predict             │
│                                      │
│   1. Load Feature Config             │
│   2. Feature Engineering Engine      │
│   3. Load Model from MLflow          │
│   4. Predict Anomaly Score           │
│   5. Return is_anomaly + score       │
└──────────┬───────────────────────────┘
           │
           │ JSON Response
           ▼
┌──────────────────────────────────────┐
│   Alert Service                      │
│   - Create Alert if anomaly=True     │
│   - Save to Database                 │
└──────────────────────────────────────┘
```

---

## 2. Architecture Components

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────┐
│                  Alert Service                      │
│  ┌───────────────────────────────────────────────┐  │
│  │  ML Evaluation Task (every 10s)               │  │
│  │  - Enumerate equipment with ML rules          │  │
│  │  - For each equipment:                        │  │
│  │    • Get feature config                       │  │
│  │    • Query Feature Store snapshot             │  │
│  │    • Call ML Service inference                │  │
│  └────────────┬──────────────────────────────────┘  │
└───────────────┼─────────────────────────────────────┘
                │
                │ Redis
                ▼
┌─────────────────────────────────────────────────────┐
│               Feature Store (Redis)                  │
│  equipment:{uuid}:sensor:{name} → SortedSet          │
│  - Stores last 100 readings per sensor              │
│  - TTL: 3600 seconds                                │
│  - Enables rolling statistics                       │
└────────────────────┬────────────────────────────────┘
                     │
                     │ Lookup current snapshot
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│              ML Service API                          │
│  POST /api/inference/predict                        │
│  {                                                  │
│    "model_id": "uuid",                              │
│    "sensor_data": {                                 │
│      "equipment_id": "uuid",                        │
│      "xmeas_1": 2.5,                                │
│      "xmeas_2": 120.3,                              │
│      ...                                            │
│    },                                               │
│    "threshold": 0.85,                               │
│    "company_id": "uuid"                             │
│  }                                                  │
└────────────────┬────────────────────────────────────┘
                 │
                 ├────────────────┐
                 │                │
                 ▼                ▼
  ┌──────────────────────┐  ┌─────────────────────┐
  │ Feature Engineering  │  │  MLflow Model       │
  │ Engine               │  │  Registry           │
  │ - Load config from   │  │  - Load model by    │
  │   database           │  │    run_id           │
  │ - Apply transforms   │  │  - Unwrap PyFunc    │
  │ - Build feature      │  │  - Get XGBoost      │
  │   vector             │  │    predict_proba    │
  └──────────┬───────────┘  └──────────┬──────────┘
             │                         │
             │ Features (numpy array)  │
             └─────────┬───────────────┘
                       │
                       ▼
            ┌────────────────────────┐
            │  Model Prediction      │
            │  - predict_proba()     │
            │  - Get probability[1]  │
            │  - Return score 0.0-1.0│
            └────────────────────────┘
```

### 2.2 Database Schema

**feature_engineering_configs Table:**
```sql
CREATE TABLE feature_engineering_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    base_sensors JSONB NOT NULL,  -- List of sensor names
    transformations JSONB NOT NULL,  -- List of transformation specs
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Example feature_engineering_configs Record:**
```json
{
  "id": "uuid",
  "name": "TEP Equipment Feature Config",
  "base_sensors": [
    "xmeas_1", "xmeas_2", ..., "xmeas_41",
    "xmv_1", "xmv_2", ..., "xmv_11"
  ],
  "transformations": [
    {
      "name": "xmeas_1_identity",
      "type": "identity",
      "sensor": "xmeas_1"
    },
    {
      "name": "xmeas_1_squared",
      "type": "polynomial",
      "sensor": "xmeas_1",
      "params": {"power": 2}
    },
    {
      "name": "xmeas_1_deviation_from_mean",
      "type": "statistical",
      "sensor": "xmeas_1",
      "params": {"stat_type": "deviation_from_run_mean"}
    }
  ]
}
```

**ml_models Table:**
```sql
CREATE TABLE ml_models (
    model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_name TEXT NOT NULL,
    model_version TEXT,
    algorithm TEXT,
    status TEXT DEFAULT 'training',  -- training|production|archived
    mlflow_run_id TEXT NOT NULL,
    feature_config_id UUID REFERENCES feature_engineering_configs(id),
    training_metrics JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**alert_rules Table (ML-specific fields):**
```sql
CREATE TABLE alert_rules (
    rule_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    detection_type TEXT CHECK (detection_type IN ('threshold', 'ml', 'statistical')),
    equipment_id UUID,  -- Required for ML rules
    model_id UUID REFERENCES ml_models(model_id),
    anomaly_threshold DOUBLE PRECISION DEFAULT 0.85,  -- 0.0-1.0
    enabled BOOLEAN DEFAULT true,
    ...
);
```

---

## 3. ML Inference Pipeline

### 3.1 Request Flow

**Step 1: Alert Service ML Evaluation Task**
```python
# Runs every 10 seconds
async def _periodic_ml_evaluation():
    # Get all equipment with ML rules
    equipment_to_evaluate = set()
    for company_id, rules in rules_engine.tenant_rules.items():
        for rule in rules:
            if rule.get('enabled') and rule.get('detection_type') == 'ml':
                equipment_id = rule.get('equipment_id')
                if equipment_id:
                    equipment_to_evaluate.add((str(equipment_id), company_id))

    # Evaluate each equipment
    for equipment_id, company_id in equipment_to_evaluate:
        alerts = await rules_engine.evaluate_equipment_ml_rules(
            equipment_id=equipment_id,
            company_id=company_id
        )
```

**Step 2: Get Feature Config and Sensor Snapshot**
```python
async def evaluate_equipment_ml_rules(equipment_id, company_id):
    # Get ML rules for this equipment
    ml_rules = filter(lambda r: r.get('equipment_id') == equipment_id
                      and r.get('detection_type') == 'ml',
                      tenant_rules[company_id])

    for rule in ml_rules:
        # Get model metadata
        model_id = rule.get('model_id')
        model_data = await repository.get_model_by_id(model_id, company_id)

        # Get feature config
        feature_config_id = model_data.get('feature_config_id')
        feature_config = await repository.get_feature_config(feature_config_id, company_id)

        # Extract base sensors list
        base_sensors = feature_config.get('base_sensors')  # ['xmeas_1', 'xmeas_2', ...]

        # Query Feature Store for current snapshot
        snapshot = await feature_store.get_current_snapshot(
            equipment_id=equipment_id,
            sensor_names=base_sensors
        )

        # Build sensor_data dict
        sensor_data = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'equipment_id': equipment_id,
            **snapshot  # {'xmeas_1': 2.5, 'xmeas_2': 120.3, ...}
        }
```

**Step 3: Call ML Inference Service**
```python
async def _evaluate_ml(sensor_data, rule, company_id):
    # Prepare request
    payload = {
        "model_id": str(rule.get('model_id')),
        "sensor_data": sensor_data,
        "threshold": rule.get('anomaly_threshold', 0.85),
        "company_id": company_id
    }

    # HTTP POST to ML Service
    async with aiohttp.ClientSession() as session:
        response = await session.post(
            f"{ML_SERVICE_URL}/api/inference/predict",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10)
        )

        result = await response.json()
        # {
        #   "model_id": "uuid",
        #   "prediction": 0.9823,  # Anomaly score
        #   "is_anomaly": true,
        #   "threshold": 0.85,
        #   "model_version": "1.0"
        # }
```

**Step 4: ML Service Processing**
```python
@router.post("/predict", response_model=InferenceResponse)
async def predict(request_data: InferenceRequest, request: Request):
    repository = request.app.state.ml_repository

    # Get model metadata
    model_data = await repository.get_model_by_id(
        company_id=request_data.company_id,
        model_id=request_data.model_id
    )

    # Load model from MLflow
    mlflow_run_id = model_data.get('mlflow_run_id')
    model = mlflow.pyfunc.load_model(f"runs:/{mlflow_run_id}/model")

    # Get feature config
    feature_config_id = model_data.get('feature_config_id')
    feature_config = await repository.get_feature_config_by_id(
        company_id=request_data.company_id,
        config_id=feature_config_id
    )

    # Create feature engineering engine
    fe_engine = FeatureEngineeringEngine(
        feature_config=feature_config,
        feature_store=request.app.state.feature_store,
        equipment_id=request_data.sensor_data.get('equipment_id')
    )

    # Transform sensor data to features
    input_features = await fe_engine.transform(request_data.sensor_data)
    # Shape: (1, 65) for 65 engineered features

    # Unwrap MLflow PyFuncModel to get XGBoost model
    actual_model = model._model_impl if hasattr(model, '_model_impl') else model

    # Get anomaly probability
    if hasattr(actual_model, 'predict_proba'):
        proba = actual_model.predict_proba(input_features)
        anomaly_score = float(proba[0][1])  # Probability of anomaly class
    else:
        # Fallback for models without predict_proba
        prediction = model.predict(input_features)
        anomaly_score = 1.0 if prediction[0] == 1 else 0.0

    # Check threshold
    is_anomaly = anomaly_score >= request_data.threshold

    return InferenceResponse(
        model_id=request_data.model_id,
        prediction=anomaly_score,
        is_anomaly=is_anomaly,
        threshold=request_data.threshold,
        model_version=model_data.get('model_version')
    )
```

### 3.2 Performance Characteristics

**Latency Breakdown:**
- Feature Store snapshot query: 5-10ms
- HTTP request to ML Service: 10-20ms
- Feature engineering: 5-15ms
- MLflow model loading (cached): <1ms
- Model prediction: 10-30ms
- Total: **30-75ms per inference**

**Throughput:**
- Single ML Service instance: 50-100 predictions/second
- Bottleneck: Feature engineering for high-dimensional features
- Scalability: Horizontal scaling via multiple ML Service instances

---

## 4. Feature Engineering System

### 4.1 Configuration Schema

**Transformation Types:**

1. **Identity** (pass-through):
```json
{
  "name": "xmeas_1_identity",
  "type": "identity",
  "sensor": "xmeas_1"
}
```

2. **Polynomial** (power transformation):
```json
{
  "name": "xmeas_1_squared",
  "type": "polynomial",
  "sensor": "xmeas_1",
  "params": {"power": 2}
}
```

3. **Interaction** (multi-sensor operations):
```json
{
  "name": "pressure_ratio",
  "type": "interaction",
  "sensors": ["xmeas_7", "xmeas_13"],
  "params": {"operation": "ratio"}  // ratio|product|sum|difference
}
```

4. **Deviation** (from baseline):
```json
{
  "name": "temp_deviation",
  "type": "deviation",
  "sensor": "xmeas_9",
  "params": {"baseline": 370.0}
}
```

5. **Statistical** (rolling statistics):
```json
{
  "name": "flow_deviation_from_mean",
  "type": "statistical",
  "sensor": "xmeas_1",
  "params": {"stat_type": "deviation_from_run_mean"}
}
```

### 4.2 Feature Engineering Engine

**Algorithm:**
```python
class FeatureEngineeringEngine:
    def __init__(self, feature_config, feature_store=None, equipment_id=None):
        self.base_sensors = feature_config.get('base_sensors', [])
        self.transformations = feature_config.get('transformations', [])
        self.feature_store = feature_store
        self.equipment_id = equipment_id

    async def transform(self, sensor_data: Dict[str, float]) -> np.ndarray:
        features = []

        for transformation in self.transformations:
            try:
                feature_value = await self._apply_transformation(
                    transformation,
                    sensor_data
                )
                features.append(feature_value)
            except Exception as e:
                logger.warning(f"Failed to compute '{transformation['name']}': {e}")
                features.append(0.0)  # Fallback

        return np.array(features).reshape(1, -1)

    async def _apply_transformation(self, transformation, sensor_data):
        t_type = transformation['type']

        if t_type == 'identity':
            sensor = transformation['sensor']
            return sensor_data.get(sensor, 0.0)

        elif t_type == 'polynomial':
            sensor = transformation['sensor']
            power = transformation['params']['power']
            value = sensor_data.get(sensor, 0.0)
            return value ** power

        elif t_type == 'statistical':
            # Deviation from rolling mean using Feature Store
            sensor = transformation['sensor']
            value = sensor_data.get(sensor, 0.0)

            if self.feature_store and self.equipment_id:
                rolling_mean = await self.feature_store.compute_rolling_mean(
                    equipment_id=self.equipment_id,
                    sensor_name=sensor,
                    window=50
                )
                if rolling_mean is not None:
                    return value - rolling_mean

            return value  # Fallback to current value

        # ... other transformation types
```

### 4.3 Feature Vector Construction

**Example: Tennessee Eastman Process**

Input sensors: 52 sensors (41 measurements + 11 manipulated variables)
Output features: 65 features

**Feature breakdown:**
- 52 identity features (pass-through)
- 5 polynomial features (squared terms for key sensors)
- 5 interaction features (pressure ratios, flow products)
- 3 statistical features (deviation from rolling mean)

**Feature vector:**
```python
[
    # Identity features (0-51)
    2.8591,  # xmeas_1
    4.5003,  # xmeas_2
    ...

    # Polynomial features (52-56)
    8.174,   # xmeas_1^2
    ...

    # Interaction features (57-61)
    0.923,   # xmeas_7 / xmeas_13
    ...

    # Statistical features (62-64)
    -0.042   # xmeas_1 - rolling_mean(xmeas_1, 50)
]
```

---

## 5. Feature Store

### 5.1 Redis Data Structure

**Key Format:**
```
equipment:{equipment_uuid}:sensor:{sensor_name}
```

**Value Format:** Sorted Set
```
ZADD equipment:550e8400-...:sensor:xmeas_1
  1732368000.123 2.5
  1732368001.234 2.6
  1732368002.345 2.4
```

**Operations:**
```python
# Store reading
ZADD equipment:{id}:sensor:{name} {timestamp} {value}

# Get latest value
ZREVRANGE equipment:{id}:sensor:{name} 0 0 WITHSCORES

# Get last N readings
ZREVRANGE equipment:{id}:sensor:{name} 0 {N-1} WITHSCORES

# Compute rolling mean
values = ZREVRANGE equipment:{id}:sensor:{name} 0 49
mean = sum(values) / len(values)

# Cleanup old data (automatic via TTL)
EXPIRE equipment:{id}:sensor:{name} 3600
```

### 5.2 Feature Store Implementation

```python
class FeatureStore:
    def __init__(self, redis_url, max_window=100, ttl_seconds=3600):
        self.redis = redis.from_url(redis_url, decode_responses=False)
        self.max_window = max_window
        self.ttl_seconds = ttl_seconds

    async def store_reading(self, equipment_id, sensor_name, timestamp, value):
        key = f"equipment:{equipment_id}:sensor:{sensor_name}"

        # Convert timestamp to float score
        ts_float = datetime.fromisoformat(timestamp).timestamp()

        # Add to sorted set
        await self.redis.zadd(key, {str(value): ts_float})

        # Trim to max window
        await self.redis.zremrangebyrank(key, 0, -(self.max_window + 1))

        # Set TTL
        await self.redis.expire(key, self.ttl_seconds)

    async def get_current_snapshot(self, equipment_id, sensor_names):
        snapshot = {}

        for sensor_name in sensor_names:
            latest = await self.get_latest(equipment_id, sensor_name)
            if latest is not None:
                snapshot[sensor_name] = latest

        return snapshot

    async def get_latest(self, equipment_id, sensor_name):
        key = f"equipment:{equipment_id}:sensor:{sensor_name}"

        # Get most recent value
        results = await self.redis.zrevrange(key, 0, 0, withscores=False)

        if results:
            return float(results[0].decode('utf-8'))

        return None

    async def compute_rolling_mean(self, equipment_id, sensor_name, window):
        key = f"equipment:{equipment_id}:sensor:{sensor_name}"

        # Get last N values
        results = await self.redis.zrevrange(key, 0, window-1, withscores=False)

        if len(results) < window // 2:  # Need at least 50% of window
            return None

        values = [float(v.decode('utf-8')) for v in results]
        return sum(values) / len(values)
```

---

## 6. Alert Service Integration

### 6.1 Periodic ML Evaluation

**Main Task Loop:**
```python
async def _periodic_ml_evaluation(self):
    # Wait for Feature Store to populate
    await asyncio.sleep(15)

    while self.running:
        # Get all equipment with ML rules
        equipment_to_evaluate = set()
        for company_id, rules in self.rules_engine.tenant_rules.items():
            for rule in rules:
                if rule.get('enabled') and rule.get('detection_type') == 'ml':
                    equipment_id = rule.get('equipment_id')
                    if equipment_id:
                        equipment_to_evaluate.add((str(equipment_id), company_id))

        # Evaluate each equipment
        if equipment_to_evaluate:
            logger.info(f"Evaluating ML rules for {len(equipment_to_evaluate)} equipment(s)")

            for equipment_id, company_id in equipment_to_evaluate:
                alerts = await self.rules_engine.evaluate_equipment_ml_rules(
                    equipment_id=equipment_id,
                    company_id=company_id
                )

                if alerts:
                    logger.info(f"Generated {len(alerts)} ML alert(s)")

        # Run every 10 seconds
        await asyncio.sleep(10)
```

### 6.2 Alert Generation

**Alert Structure:**
```python
alert = {
    'company_id': company_id,
    'rule_id': str(rule['rule_id']),
    'sensor_id': None,  # Equipment-level, no single sensor
    'equipment_id': str(equipment_id),
    'site_id': sensor_data.get('site_id'),
    'triggered_at': timestamp,
    'detection_type': 'ml',
    'actual_value': None,  # Multi-sensor, no single value
    'anomaly_score': 0.9823,  # From ML model
    'model_id': str(model_id),
    'threshold_value': 0.85,
    'condition': 'ml_anomaly',
    'severity': rule.get('severity', 'medium'),
    'message': f"ML anomaly detected: {rule['name']} - Anomaly score 0.9823 exceeds threshold 0.85"
}
```

---

## 7. Implementation Details

### 7.1 MLflow Model Wrapper Fix

**Problem:**
MLflow's `PyFuncModel` wrapper doesn't expose the underlying model's `predict_proba()` method, causing all anomaly scores to be 0.0.

**Solution:**
```python
# Unwrap MLflow model to get underlying XGBoost model
actual_model = model
if hasattr(model, '_model_impl'):
    actual_model = model._model_impl  # sklearn._SklearnModelWrapper

# Now predict_proba is available
if hasattr(actual_model, 'predict_proba'):
    proba = actual_model.predict_proba(input_features)
    anomaly_score = float(proba[0][1])  # Probability of class 1 (anomaly)
```

### 7.2 XGBoost vs IsolationForest Conventions

**XGBoost Binary Classification:**
- Class 0 = Normal
- Class 1 = Anomaly
- `predict()` returns class label: [0] or [1]
- `predict_proba()` returns probabilities: [[p_normal, p_anomaly]]

**IsolationForest:**
- Prediction 1 = Normal
- Prediction -1 = Anomaly
- Only `predict()` available, no `predict_proba()`

**Unified Handling:**
```python
if hasattr(actual_model, 'predict_proba'):
    # XGBoost or sklearn with probability support
    proba = actual_model.predict_proba(input_features)
    anomaly_score = float(proba[0][1])
elif isinstance(prediction[0], (int, np.integer)):
    # Handle both conventions
    if prediction[0] == -1:  # IsolationForest
        anomaly_score = 1.0
    elif prediction[0] == 1:  # XGBoost or IsolationForest
        # Ambiguous - default to XGBoost convention (1 = anomaly)
        anomaly_score = 1.0
    elif prediction[0] == 0:  # XGBoost
        anomaly_score = 0.0
else:
    # Direct score
    anomaly_score = float(prediction[0])
```

### 7.3 JSONB Field Parsing

**Problem:**
PostgreSQL JSONB fields returned by asyncpg as strings, not lists.

**Solution:**
```python
base_sensors = feature_config.get('base_sensors', [])

# Check if string (JSONB serialization issue)
if isinstance(base_sensors, str):
    import json
    base_sensors = json.loads(base_sensors)
```

### 7.4 UUID Handling

**Problem:**
asyncpg returns UUIDs as `asyncpg.pgproto.pgproto.UUID` objects, not strings.

**Solution:**
```python
# Convert UUID to string for string operations
equipment_id = str(rule.get('equipment_id'))
model_id = str(rule.get('model_id'))

# Normalize company_id for schema name
schema_name = normalize_company_id_to_schema(str(company_id))
```

---

## 8. Troubleshooting Guide

### 8.1 No ML Alerts Generated

**Symptoms:**
- ML rules created and enabled
- Mock data shows anomalies
- No alerts in database

**Debug Steps:**

1. **Check ML evaluation task is running:**
```bash
docker logs industryflow-alert-service-detector 2>&1 | grep "Evaluating ML rules"
```
Expected: Log every 10 seconds with equipment count

2. **Check Feature Store has data:**
```bash
docker exec industryflow-redis redis-cli
> KEYS equipment:*
> ZRANGE equipment:{uuid}:sensor:xmeas_1 0 -1 WITHSCORES
```
Expected: Keys exist with recent timestamps

3. **Check ML inference is being called:**
```bash
docker logs industryflow-alert-service-detector 2>&1 | grep "Calling ML inference"
```

4. **Check ML Service logs:**
```bash
docker logs industryflow-ml-service-api 2>&1 | grep "Inference complete"
```
Expected: `score=0.XXXX, anomaly=True/False`

5. **Check anomaly scores:**
```bash
docker logs industryflow-ml-service-api 2>&1 | grep "Inference complete" | tail -10
```
If all scores are 0.0000 → MLflow wrapper issue
If scores are correct but anomaly=False → Threshold too high

### 8.2 Anomaly Score Always 0.0

**Cause:** MLflow PyFuncModel doesn't expose `predict_proba()`

**Fix:**
Check inference.py lines 214-221 for model unwrapping:
```python
actual_model = model._model_impl if hasattr(model, '_model_impl') else model
```

**Verify Fix:**
```bash
docker logs industryflow-ml-service-api 2>&1 | grep "predict_proba"
```
Should show predict_proba being called with probability values

### 8.3 Incomplete Sensor Snapshot

**Symptoms:**
```
Incomplete sensor snapshot for equipment ...: 0/52 sensors available
```

**Causes:**
1. Feature Store not populated yet (wait 15-30 seconds after startup)
2. Sensor names don't match between data and feature config
3. Feature Store keys expired (TTL too short)

**Debug:**
```python
# Check sensor names in Feature Store
for key in redis_cli.keys('equipment:{uuid}:sensor:*'):
    print(key.decode('utf-8'))

# Check feature config base_sensors
SELECT base_sensors FROM feature_engineering_configs WHERE id = '{uuid}';
```

**Fix:**
Ensure sensor names in Kafka messages match `base_sensors` list exactly.

### 8.4 Model Not Found Error

**Symptoms:**
```
Model {uuid} not found for ML evaluation
```

**Causes:**
1. Model not deployed (status != 'production')
2. Model missing mlflow_run_id
3. Feature config not linked to model

**Debug:**
```sql
SELECT model_id, status, mlflow_run_id, feature_config_id
FROM ml_models
WHERE model_id = '{uuid}';
```

**Fix:**
```sql
UPDATE ml_models
SET status = 'production'
WHERE model_id = '{uuid}';
```

### 8.5 High Latency (>500ms)

**Causes:**
1. MLflow model not cached (first load)
2. Feature Store query slow (Redis connection)
3. Too many features (high-dimensional feature vector)
4. Database query slow (feature config retrieval)

**Optimization:**
1. Warm up models at startup
2. Use Redis connection pooling
3. Reduce feature count or use feature selection
4. Cache feature configs in memory

**Monitor:**
```bash
docker logs industryflow-ml-service-api 2>&1 | grep "Inference complete" | \
  awk '{print $(NF-3)}' | grep -oP '\d+\.\d+' | \
  awk '{sum+=$1; count++} END {print "Avg latency:", sum/count, "ms"}'
```

---

**END OF DOCUMENTATION**
