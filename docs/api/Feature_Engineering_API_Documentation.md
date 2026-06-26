<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Feature Engineering API Documentation

**Service:** ML Service API
**Base URL:** `http://localhost:8002`
**Version:** 2.0.0
**Authentication:** JWT Bearer Token
**Date:** November 2025

---

## Table of Contents

1. [Overview](#1-overview)
2. [Authentication](#2-authentication)
3. [API Endpoints](#3-api-endpoints)
4. [Request/Response Examples](#4-requestresponse-examples)
5. [Error Codes](#5-error-codes)
6. [Usage Examples](#6-usage-examples)

---

## 1. Overview

The Feature Engineering API provides endpoints for managing feature transformation configurations used in ML model training and production inference. These configurations define how raw sensor data is transformed into engineered features for machine learning models.

### Key Features

- **Declarative Configuration:** JSON-based transformation definitions
- **Versioning:** Track feature engineering evolution over time
- **Multi-Tenant:** Complete isolation between company data
- **Integration:** Seamless linkage with ML models via foreign keys
- **Reproducibility:** Identical transformations from training to production

### Transformation Types Supported

1. **Identity:** Direct sensor value pass-through
2. **Polynomial:** Power transformations (x², x³, etc.)
3. **Interaction:** Multi-sensor operations (ratio, difference, product)
4. **Statistical:** Deviation from baseline values

---

## 2. Authentication

All API endpoints require JWT authentication.

### Request Headers

```http
Authorization: Bearer <JWT_TOKEN>
Content-Type: application/json
```

### JWT Token Structure

```json
{
  "sub": "user_id (UUID)",
  "aud": ["fastapi-users:auth"],
  "exp": 1764409412
}
```

### Error Response (Unauthorized)

```json
HTTP/1.1 401 Unauthorized
{
  "detail": "Not authenticated"
}
```

---

## 3. API Endpoints

### 3.1 Create Feature Configuration

**Endpoint:** `POST /api/feature-configs`

**Description:** Register a new feature engineering configuration

**Request Body:**
```json
{
  "name": "string (required)",
  "description": "string (optional)",
  "equipment_type": "string (required)",
  "base_sensors": ["string"] (required),
  "transformations": [
    {
      "name": "string",
      "type": "identity|polynomial|interaction|statistical",
      "sensor": "string (for identity, polynomial, statistical)",
      "sensors": ["string"] (for interaction),
      "params": {
        "power": integer (for polynomial),
        "operation": "ratio|difference|product" (for interaction),
        "stat_type": "deviation_from_run_mean" (for statistical)
      }
    }
  ],
  "version": "string (required, semver format)",
  "status": "active|deprecated|draft (default: active)"
}
```

**Response:**
```json
HTTP/1.1 201 Created
{
  "id": "uuid",
  "company_id": "uuid",
  "name": "string",
  "description": "string",
  "equipment_type": "string",
  "base_sensors": ["string"],
  "transformations": [...],
  "version": "string",
  "status": "string",
  "created_at": "timestamp",
  "updated_at": "timestamp",
  "created_by": "uuid"
}
```

**Example:**
```bash
curl -X POST http://localhost:8002/api/feature-configs \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d @feature_config.json
```

---

### 3.2 Get Feature Configuration by ID

**Endpoint:** `GET /api/feature-configs/{config_id}`

**Description:** Retrieve a specific feature engineering configuration

**Path Parameters:**
- `config_id` (uuid, required): Feature configuration ID

**Response:**
```json
HTTP/1.1 200 OK
{
  "id": "uuid",
  "company_id": "uuid",
  "name": "string",
  "description": "string",
  "equipment_type": "string",
  "base_sensors": ["string"],
  "transformations": [...],
  "version": "string",
  "status": "string",
  "created_at": "timestamp",
  "updated_at": "timestamp",
  "created_by": "uuid"
}
```

**Error Responses:**
```json
HTTP/1.1 404 Not Found
{
  "detail": "Feature config not found"
}
```

**Example:**
```bash
curl -X GET http://localhost:8002/api/feature-configs/3d456f9b-acfc-4e16-9f3b-dcd65da9430f \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

---

### 3.3 List Feature Configurations

**Endpoint:** `GET /api/feature-configs`

**Description:** List all feature configurations with optional filtering

**Query Parameters:**
- `equipment_type` (string, optional): Filter by equipment type
- `status` (string, optional, default: "active"): Filter by status

**Response:**
```json
HTTP/1.1 200 OK
{
  "feature_configs": [
    {
      "id": "uuid",
      "company_id": "uuid",
      "name": "string",
      "equipment_type": "string",
      "base_sensors": ["string"],
      "transformations": [...],
      "version": "string",
      "status": "string",
      "created_at": "timestamp",
      "updated_at": "timestamp"
    }
  ],
  "total": integer
}
```

**Examples:**
```bash
# Get all active configs
curl -X GET http://localhost:8002/api/feature-configs \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Filter by equipment type
curl -X GET "http://localhost:8002/api/feature-configs?equipment_type=tep_reactor" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# Get deprecated configs
curl -X GET "http://localhost:8002/api/feature-configs?status=deprecated" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

---

### 3.4 Update Feature Configuration Status

**Endpoint:** `PUT /api/feature-configs/{config_id}/status`

**Description:** Update configuration status (active/deprecated/draft)

**Path Parameters:**
- `config_id` (uuid, required): Feature configuration ID

**Request Body:**
```json
{
  "status": "active|deprecated|draft"
}
```

**Response:**
```json
HTTP/1.1 200 OK
{
  "id": "uuid",
  "status": "string",
  "updated_at": "timestamp"
}
```

**Example:**
```bash
curl -X PUT http://localhost:8002/api/feature-configs/3d456f9b-acfc-4e16-9f3b-dcd65da9430f/status \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "deprecated"}'
```

---

## 4. Request/Response Examples

### 4.1 Complete Feature Configuration Example

**Tennessee Eastman Process (TEP) Reactor Configuration**

```json
{
  "name": "TEP Binary Anomaly Detection - Balanced 50/50",
  "description": "Feature engineering for TEP binary anomaly detection with balanced dataset",
  "equipment_type": "tep_reactor",
  "base_sensors": [
    "xmeas_1", "xmeas_2", "xmeas_3", "xmeas_4", "xmeas_5", "xmeas_6",
    "xmeas_7", "xmeas_8", "xmeas_9", "xmeas_10", "xmeas_11", "xmeas_12",
    "xmeas_13", "xmeas_14", "xmeas_15", "xmeas_16", "xmeas_17", "xmeas_18",
    "xmeas_19", "xmeas_20", "xmeas_21", "xmeas_22", "xmeas_23", "xmeas_24",
    "xmeas_25", "xmeas_26", "xmeas_27", "xmeas_28", "xmeas_29", "xmeas_30",
    "xmeas_31", "xmeas_32", "xmeas_33", "xmeas_34", "xmeas_35", "xmeas_36",
    "xmeas_37", "xmeas_38", "xmeas_39", "xmeas_40", "xmeas_41",
    "xmv_1", "xmv_2", "xmv_3", "xmv_4", "xmv_5", "xmv_6",
    "xmv_7", "xmv_8", "xmv_9", "xmv_10", "xmv_11"
  ],
  "transformations": [
    {
      "name": "xmeas_1",
      "type": "identity",
      "sensor": "xmeas_1"
    },
    {
      "name": "xmeas_7_squared",
      "type": "polynomial",
      "sensor": "xmeas_7",
      "params": {"power": 2}
    },
    {
      "name": "xmeas_7_cubed",
      "type": "polynomial",
      "sensor": "xmeas_7",
      "params": {"power": 3}
    },
    {
      "name": "xmeas_1_xmeas_2_ratio",
      "type": "interaction",
      "sensors": ["xmeas_1", "xmeas_2"],
      "params": {"operation": "ratio"}
    },
    {
      "name": "xmeas_7_xmeas_8_diff",
      "type": "interaction",
      "sensors": ["xmeas_7", "xmeas_8"],
      "params": {"operation": "difference"}
    },
    {
      "name": "xmeas_9_xmeas_10_product",
      "type": "interaction",
      "sensors": ["xmeas_9", "xmeas_10"],
      "params": {"operation": "product"}
    },
    {
      "name": "xmeas_1_deviation",
      "type": "statistical",
      "sensor": "xmeas_1",
      "params": {"stat_type": "deviation_from_run_mean"}
    }
  ],
  "version": "1.0.0",
  "status": "active"
}
```

### 4.2 Transformation Type Examples

#### Identity Transformation
```json
{
  "name": "temperature_sensor_1",
  "type": "identity",
  "sensor": "temp_1"
}
```
**Result:** Direct sensor value (e.g., 120.5°C)

#### Polynomial Transformation
```json
{
  "name": "pressure_squared",
  "type": "polynomial",
  "sensor": "pressure_1",
  "params": {"power": 2}
}
```
**Example:** Input: 2630 → Output: 6,916,900

#### Interaction - Ratio
```json
{
  "name": "pressure_to_level_ratio",
  "type": "interaction",
  "sensors": ["pressure_1", "level_1"],
  "params": {"operation": "ratio"}
}
```
**Example:** Pressure: 2630, Level: 75 → Output: 35.07

#### Interaction - Difference
```json
{
  "name": "inlet_outlet_temp_diff",
  "type": "interaction",
  "sensors": ["temp_inlet", "temp_outlet"],
  "params": {"operation": "difference"}
}
```
**Example:** Inlet: 120°C, Outlet: 95°C → Output: 25°C

#### Interaction - Product
```json
{
  "name": "flow_pressure_product",
  "type": "interaction",
  "sensors": ["flow_rate", "pressure"],
  "params": {"operation": "product"}
}
```
**Example:** Flow: 50 m³/h, Pressure: 100 bar → Output: 5000

#### Statistical Transformation
```json
{
  "name": "temperature_deviation",
  "type": "statistical",
  "sensor": "temperature_1",
  "params": {"stat_type": "deviation_from_run_mean"}
}
```
**Example:** Current: 122°C, Baseline: 120°C → Output: +2°C

---

## 5. Error Codes

### 5.1 HTTP Status Codes

| Code | Description | Common Causes |
|------|-------------|---------------|
| 200 | OK | Successful GET request |
| 201 | Created | Successful POST request |
| 400 | Bad Request | Invalid JSON, missing required fields |
| 401 | Unauthorized | Missing or invalid JWT token |
| 404 | Not Found | Config ID doesn't exist or wrong tenant |
| 422 | Unprocessable Entity | Validation error (Pydantic) |
| 500 | Internal Server Error | Database error, unexpected exception |

### 5.2 Error Response Format

```json
{
  "detail": "Error message string"
}
```

### 5.3 Validation Errors (422)

```json
{
  "detail": [
    {
      "loc": ["body", "field_name"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### 5.4 Common Error Examples

**Missing Authorization Header:**
```json
HTTP/1.1 401 Unauthorized
{
  "detail": "Not authenticated"
}
```

**Invalid UUID:**
```json
HTTP/1.1 422 Unprocessable Entity
{
  "detail": [
    {
      "loc": ["path", "config_id"],
      "msg": "value is not a valid uuid",
      "type": "type_error.uuid"
    }
  ]
}
```

**Config Not Found:**
```json
HTTP/1.1 404 Not Found
{
  "detail": "Feature config not found"
}
```

**Invalid Transformation Type:**
```json
HTTP/1.1 400 Bad Request
{
  "detail": "Invalid transformation type. Must be: identity, polynomial, interaction, or statistical"
}
```

**Missing Required Field:**
```json
HTTP/1.1 422 Unprocessable Entity
{
  "detail": [
    {
      "loc": ["body", "equipment_type"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## 6. Usage Examples

### 6.1 Jupyter Notebook Integration

**Step 1: Define Feature Configuration in Notebook**

```python
import requests
import json

# Configuration
ML_SERVICE_URL = "http://ml-service-api:8002"
JWT_TOKEN = "your_jwt_token_here"

headers = {
    "Authorization": f"Bearer {JWT_TOKEN}",
    "Content-Type": "application/json"
}

# Define feature engineering configuration
feature_config = {
    "name": "My Equipment Anomaly Detection Config",
    "equipment_type": "reactor",
    "base_sensors": [
        "temperature_1", "pressure_1", "flow_rate_1",
        "level_1", "vibration_1"
    ],
    "transformations": [
        # Identity features
        {"name": "temperature_1", "type": "identity", "sensor": "temperature_1"},
        {"name": "pressure_1", "type": "identity", "sensor": "pressure_1"},

        # Polynomial features
        {"name": "pressure_squared", "type": "polynomial",
         "sensor": "pressure_1", "params": {"power": 2}},

        # Interaction features
        {"name": "temp_pressure_ratio", "type": "interaction",
         "sensors": ["temperature_1", "pressure_1"],
         "params": {"operation": "ratio"}},

        # Statistical features
        {"name": "temperature_deviation", "type": "statistical",
         "sensor": "temperature_1",
         "params": {"stat_type": "deviation_from_run_mean"}}
    ],
    "version": "1.0.0",
    "status": "active"
}

# Register feature config
response = requests.post(
    f"{ML_SERVICE_URL}/api/feature-configs",
    headers=headers,
    json=feature_config
)

if response.status_code == 201:
    feature_config_id = response.json()['id']
    print(f"✓ Feature Config ID: {feature_config_id}")
else:
    print(f"✗ Error: {response.text}")
```

**Step 2: Train Model with Feature Config**

```python
import pandas as pd
import mlflow
from sklearn.model_selection import train_test_split
import xgboost as xgb

# Load raw data
raw_data = pd.read_csv('equipment_data.csv')

# Apply feature engineering (same as production will use)
def apply_transformations(row, feature_config):
    features = {}
    for transform in feature_config['transformations']:
        name = transform['name']
        trans_type = transform['type']

        if trans_type == 'identity':
            features[name] = row[transform['sensor']]
        elif trans_type == 'polynomial':
            sensor = transform['sensor']
            power = transform['params']['power']
            features[name] = row[sensor] ** power
        # ... implement other types ...

    return features

# Engineer features
X = raw_data.apply(
    lambda row: apply_transformations(row, feature_config),
    axis=1, result_type='expand'
)
y = raw_data['is_anomaly']

# Train model
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3)

mlflow.start_run()
model = xgb.XGBClassifier(...)
model.fit(X_train, y_train)

# Log to MLflow
mlflow.sklearn.log_model(model, "model")
mlflow_run_id = mlflow.active_run().info.run_id

print(f"✓ MLflow Run ID: {mlflow_run_id}")
```

**Step 3: Register Model with Feature Config Link**

```python
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

# Evaluate model
y_pred = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]

# Prepare model metadata
model_data = {
    "model_name": "Equipment Anomaly Detector",
    "equipment_type": "reactor",
    "model_type": "xgboost",
    "model_version": "1.0.0",
    "status": "production",
    "mlflow_run_id": mlflow_run_id,
    "mlflow_experiment_id": "1",
    "feature_config_id": feature_config_id,  # Link to feature config!

    "accuracy": float(accuracy_score(y_test, y_pred)),
    "precision_score": float(precision_score(y_test, y_pred)),
    "recall": float(recall_score(y_test, y_pred)),
    "f1_score": float(f1_score(y_test, y_pred)),
    "auc_roc": float(roc_auc_score(y_test, y_proba)),

    "hyperparameters": model.get_params(),
    "feature_names": list(X.columns)
}

# Register model
response = requests.post(
    f"{ML_SERVICE_URL}/api/models",
    headers=headers,
    json=model_data
)

if response.status_code == 201:
    model_id = response.json()['model_id']
    print(f"✓ Model ID: {model_id}")
    print(f"✓ Linked to Feature Config: {feature_config_id}")
else:
    print(f"✗ Error: {response.text}")
```

### 6.2 Production Inference Integration

```python
import requests
import mlflow
import pandas as pd

# Configuration
ML_SERVICE_URL = "http://ml-service-api:8002"
MLFLOW_URI = "http://mlflow:5000"
JWT_TOKEN = "your_jwt_token_here"
MODEL_ID = "your_model_id_here"

mlflow.set_tracking_uri(MLFLOW_URI)
headers = {"Authorization": f"Bearer {JWT_TOKEN}"}

# Step 1: Retrieve model metadata
response = requests.get(f"{ML_SERVICE_URL}/api/models/{MODEL_ID}", headers=headers)
model_metadata = response.json()

# Step 2: Retrieve feature engineering config
response = requests.get(
    f"{ML_SERVICE_URL}/api/feature-configs/{model_metadata['feature_config_id']}",
    headers=headers
)
feature_config = response.json()

# Step 3: Load model from MLflow
model = mlflow.sklearn.load_model(f"runs:/{model_metadata['mlflow_run_id']}/model")

# Step 4: Process streaming sensor data
def process_sensor_reading(sensor_data):
    # Apply feature engineering (same as training!)
    engineered_features = {}
    for transform in feature_config['transformations']:
        name = transform['name']
        trans_type = transform['type']

        if trans_type == 'identity':
            engineered_features[name] = sensor_data[transform['sensor']]
        elif trans_type == 'polynomial':
            sensor = transform['sensor']
            power = transform['params']['power']
            engineered_features[name] = sensor_data[sensor] ** power
        elif trans_type == 'interaction':
            s1, s2 = transform['sensors']
            op = transform['params']['operation']
            if op == 'ratio':
                engineered_features[name] = sensor_data[s1] / (sensor_data[s2] + 1e-10)
            elif op == 'difference':
                engineered_features[name] = sensor_data[s1] - sensor_data[s2]
            elif op == 'product':
                engineered_features[name] = sensor_data[s1] * sensor_data[s2]
        elif trans_type == 'statistical':
            sensor = transform['sensor']
            baseline = BASELINE_MEANS.get(sensor, 0.0)
            engineered_features[name] = sensor_data[sensor] - baseline

    # Order features according to model
    feature_vector = [engineered_features[name] for name in model_metadata['feature_names']]

    # Convert to DataFrame for sklearn
    feature_df = pd.DataFrame([feature_vector], columns=model_metadata['feature_names'])

    # Run inference
    prediction = model.predict(feature_df)[0]
    probability = model.predict_proba(feature_df)[0][1]

    return {
        "prediction": int(prediction),
        "anomaly_probability": float(probability),
        "is_anomaly": prediction == 1
    }

# Step 5: Real-time processing
for sensor_reading in sensor_stream:
    result = process_sensor_reading(sensor_reading)

    if result['is_anomaly']:
        print(f"⚠️ ANOMALY DETECTED! Probability: {result['anomaly_probability']:.2%}")
        trigger_alert(result)
    else:
        print(f"✓ Normal operation. Probability: {result['anomaly_probability']:.2%}")
```

### 6.3 Version Management

**Create New Version of Feature Config:**

```python
# Retrieve current config
response = requests.get(
    f"{ML_SERVICE_URL}/api/feature-configs/{old_config_id}",
    headers=headers
)
old_config = response.json()

# Deprecate old version
requests.put(
    f"{ML_SERVICE_URL}/api/feature-configs/{old_config_id}/status",
    headers=headers,
    json={"status": "deprecated"}
)

# Create new version with improvements
new_config = {
    "name": old_config['name'] + " v2.0",
    "equipment_type": old_config['equipment_type'],
    "base_sensors": old_config['base_sensors'],
    "transformations": old_config['transformations'] + [
        # Add new transformations
        {"name": "new_feature_1", "type": "polynomial",
         "sensor": "sensor_x", "params": {"power": 3}},
        {"name": "new_feature_2", "type": "interaction",
         "sensors": ["sensor_y", "sensor_z"],
         "params": {"operation": "ratio"}}
    ],
    "version": "2.0.0",  # Increment version
    "status": "active"
}

# Register new version
response = requests.post(
    f"{ML_SERVICE_URL}/api/feature-configs",
    headers=headers,
    json=new_config
)
new_config_id = response.json()['id']

print(f"✓ Old config {old_config_id}: deprecated")
print(f"✓ New config {new_config_id}: active")
```

### 6.4 A/B Testing with Different Feature Configs

```python
# Model A: Uses feature config v1.0 (65 features)
model_a_id = "uuid_model_a"
config_a_id = "uuid_config_v1"

# Model B: Uses feature config v2.0 (80 features)
model_b_id = "uuid_model_b"
config_b_id = "uuid_config_v2"

# Route 50% traffic to each model
def get_model_for_request(user_id):
    if hash(user_id) % 2 == 0:
        return model_a_id, config_a_id
    else:
        return model_b_id, config_b_id

# Process request
model_id, config_id = get_model_for_request(request.user_id)

# Both models use their respective feature configs
# Ensures fair comparison - each uses its optimized transformations
result = run_inference(sensor_data, model_id, config_id)

# Log results for comparison
log_ab_test_result(model_id, config_id, result)
```

---

## 7. Best Practices

### 7.1 Feature Configuration Design

**Naming Convention:**
```
Format: {sensor}_{transformation}_{params}
Examples:
  - xmeas_1 (identity)
  - xmeas_7_squared (polynomial, power=2)
  - xmeas_7_xmeas_8_ratio (interaction, ratio)
  - xmeas_1_deviation (statistical, deviation)
```

**Versioning:**
```
Use semantic versioning: MAJOR.MINOR.PATCH
  - MAJOR: Incompatible changes (different sensors, major restructure)
  - MINOR: Add new features (backward compatible)
  - PATCH: Bug fixes, parameter tuning
```

**Status Lifecycle:**
```
draft → active → deprecated
  - draft: Under development/testing
  - active: Used in production
  - deprecated: Replaced by newer version
```

### 7.2 Performance Optimization

**Caching:**
```python
# Cache feature configs to avoid repeated database queries
from functools import lru_cache

@lru_cache(maxsize=100)
def get_feature_config_cached(config_id):
    return requests.get(
        f"{ML_SERVICE_URL}/api/feature-configs/{config_id}",
        headers=headers
    ).json()
```

**Batch Processing:**
```python
# Process multiple samples at once for better performance
def batch_engineer_features(sensor_data_df, feature_config):
    # Use vectorized NumPy operations (16x faster than loops)
    import numpy as np

    features = {}
    for transform in feature_config['transformations']:
        if transform['type'] == 'polynomial':
            sensor = transform['sensor']
            power = transform['params']['power']
            features[transform['name']] = sensor_data_df[sensor].values ** power
        # ... other types ...

    return pd.DataFrame(features)
```

### 7.3 Error Handling

**Robust Inference Code:**
```python
def safe_apply_transformation(sensor_data, transform):
    try:
        return apply_transformation(sensor_data, transform)
    except KeyError as e:
        # Missing sensor - use default value
        logger.warning(f"Missing sensor {e}, using default 0.0")
        return 0.0
    except ZeroDivisionError:
        # Division by zero in ratio
        logger.warning(f"Division by zero in {transform['name']}, using 0.0")
        return 0.0
    except Exception as e:
        # Unexpected error
        logger.error(f"Error in transformation {transform['name']}: {e}")
        return 0.0
```

### 7.4 Testing

**Unit Test Transformations:**
```python
import pytest

def test_identity_transformation():
    sensor_data = {"temp_1": 120.5}
    transform = {"name": "temp_1", "type": "identity", "sensor": "temp_1"}
    assert apply_transformation(sensor_data, transform) == 120.5

def test_polynomial_transformation():
    sensor_data = {"pressure_1": 10}
    transform = {"name": "pressure_squared", "type": "polynomial",
                "sensor": "pressure_1", "params": {"power": 2}}
    assert apply_transformation(sensor_data, transform) == 100

def test_interaction_ratio():
    sensor_data = {"s1": 100, "s2": 50}
    transform = {"name": "s1_s2_ratio", "type": "interaction",
                "sensors": ["s1", "s2"], "params": {"operation": "ratio"}}
    assert apply_transformation(sensor_data, transform) == 2.0
```

---

## 8. Support

### 8.1 Common Issues

**Issue: Config not found after creation**
- **Cause:** Wrong tenant/company_id
- **Solution:** Verify JWT token belongs to correct company

**Issue: Feature count mismatch**
- **Cause:** Model expects different number of features than config provides
- **Solution:** Ensure model.feature_names matches transformations in config

**Issue: Division by zero in ratio operation**
- **Cause:** Denominator sensor is 0
- **Solution:** Add small epsilon (1e-10) to denominator

**Issue: Slow inference**
- **Cause:** Re-fetching config for every prediction
- **Solution:** Cache feature configs, load model once at startup

### 8.2 Contact

For issues, questions, or feature requests:
- **GitHub Issues:** https://github.com/your-org/industryflow/issues
- **Documentation:** https://docs.industryflow.io
- **Email:** support@industryflow.io

---

**Document Version:** 1.0
**Last Updated:** November 22, 2025
**Maintained By:** ML Service Development Team
