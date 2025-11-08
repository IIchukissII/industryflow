# Alert Service API - Usage Documentation

**Version:** 2.0.0  
**Base URL:** `http://localhost:8001`  
**Authentication:** JWT Bearer Token  
**Architecture:** Schema-per-tenant (multi-tenant isolation)  
**Date:** November 8, 2025

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [Alert Rules Endpoints](#2-alert-rules-endpoints)
3. [ML Models Endpoints](#3-ml-models-endpoints)
4. [Alerts History Endpoints](#4-alerts-history-endpoints)
5. [Data Models](#5-data-models)
6. [Error Responses](#6-error-responses)
7. [Usage Examples](#7-usage-examples)

---

## 1. Authentication

### 1.1 Obtaining JWT Token

**Step 1:** Login via API Gateway to obtain JWT token:

```bash
curl -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@acme.com&password=SecurePass123!"
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### 1.2 Using Token

Include token in `Authorization` header for all requests:

```
Authorization: Bearer {access_token}
```

### 1.3 Token Contents

Token contains:
- `sub`: User UUID
- `aud`: Audience claims
- `exp`: Expiration timestamp

**Note:** Company ID is resolved automatically via database lookup based on user ID.

---

## 2. Alert Rules Endpoints

### 2.1 List Alert Rules

**Endpoint:** `GET /api/alert-rules`  
**Description:** Retrieve all alert rules for authenticated user's company.

**Request:**
```bash
curl -X GET http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer {token}"
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| detection_type | string | No | Filter by: threshold, ml_model, hybrid, disabled |

**Response:** `200 OK`
```json
[
  {
    "rule_id": "7fd78efb-2587-4dc6-970d-fed95308663b",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "High Temperature Alert",
    "description": null,
    "sensor_pattern": null,
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "equipment_id": null,
    "site_id": null,
    "detection_type": "threshold",
    "condition": null,
    "threshold": 80.0,
    "threshold_min": null,
    "threshold_max": null,
    "model_id": null,
    "anomaly_threshold": 0.7,
    "ml_config": null,
    "severity": "high",
    "priority": 3,
    "enabled": true,
    "created_at": "2025-11-08T01:22:42.721575Z",
    "updated_at": "2025-11-08T01:22:42.721575Z",
    "created_by": null
  }
]
```

---

### 2.2 Get Single Alert Rule

**Endpoint:** `GET /api/alert-rules/{rule_id}`  
**Description:** Retrieve specific alert rule by ID.

**Request:**
```bash
curl -X GET http://localhost:8001/api/alert-rules/7fd78efb-2587-4dc6-970d-fed95308663b \
  -H "Authorization: Bearer {token}"
```

**Response:** `200 OK` (same structure as list item)

**Error Responses:**
- `404 Not Found` - Rule does not exist
- `401 Unauthorized` - Invalid or missing token

---

### 2.3 Create Alert Rule

**Endpoint:** `POST /api/alert-rules`  
**Description:** Create new alert rule for authenticated user's company.

**Request:**
```bash
curl -X POST http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Low Pressure Alert",
    "description": "Triggers when pressure drops below threshold",
    "sensor_id": "550e8400-e29b-41d4-a716-446655440021",
    "detection_type": "threshold",
    "threshold": 50.0,
    "severity": "critical",
    "priority": 5,
    "enabled": true
  }'
```

**Request Body Fields:**

**Required:**
| Field | Type | Description |
|-------|------|-------------|
| name | string | Rule name (unique per company) |
| detection_type | enum | threshold, ml_model, hybrid, disabled |
| severity | enum | low, medium, high, critical |

**Optional:**
| Field | Type | Description |
|-------|------|-------------|
| description | string | Rule description |
| sensor_pattern | string | Regex pattern for sensor matching |
| sensor_id | UUID | Specific sensor UUID |
| equipment_id | UUID | Apply to all sensors on equipment |
| site_id | string | Site identifier |
| condition | enum | greater_than, less_than, equals, not_equals, between, outside_range |
| threshold | float | Single threshold value |
| threshold_min | float | Lower bound for range |
| threshold_max | float | Upper bound for range |
| model_id | UUID | ML model reference (for ml_model/hybrid types) |
| anomaly_threshold | float | ML anomaly score threshold (0.0-1.0, default: 0.85) |
| ml_config | object | ML model configuration JSON |
| priority | integer | Rule priority (default: 0) |
| enabled | boolean | Active status (default: true) |
| created_by | string | Creator identifier |

**Response:** `201 Created`
```json
{
  "rule_id": "0f4c8a66-68f6-4cff-b4e1-6e1d5862105f",
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Low Pressure Alert",
  "description": "Triggers when pressure drops below threshold",
  "sensor_id": "550e8400-e29b-41d4-a716-446655440021",
  "detection_type": "threshold",
  "threshold": 50.0,
  "severity": "critical",
  "priority": 5,
  "enabled": true,
  "created_at": "2025-11-08T13:14:52.421096Z",
  "updated_at": "2025-11-08T13:14:52.421096Z",
  "created_by": null
}
```

**Detection Type Requirements:**

**Threshold Mode:**
- Must specify: `condition` and (`threshold` OR `threshold_min`/`threshold_max`)
- Example: `{"condition": "greater_than", "threshold": 80.0}`

**ML Model Mode:**
- Must specify: `model_id`
- Optional: `anomaly_threshold` (default: 0.85)

**Hybrid Mode:**
- Must specify both threshold and ML parameters
- Triggers if EITHER condition is met

---

### 2.4 Update Alert Rule

**Endpoint:** `PUT /api/alert-rules/{rule_id}`  
**Description:** Update existing alert rule. All fields optional.

**Request:**
```bash
curl -X PUT http://localhost:8001/api/alert-rules/0f4c8a66-68f6-4cff-b4e1-6e1d5862105f \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Low Pressure Alert",
    "threshold": 45.0,
    "priority": 10
  }'
```

**Request Body:** Same fields as Create (all optional)

**Response:** `200 OK` (updated rule object)

---

### 2.5 Delete Alert Rule

**Endpoint:** `DELETE /api/alert-rules/{rule_id}`  
**Description:** Delete alert rule permanently.

**Request:**
```bash
curl -X DELETE http://localhost:8001/api/alert-rules/0f4c8a66-68f6-4cff-b4e1-6e1d5862105f \
  -H "Authorization: Bearer {token}"
```

**Response:** `204 No Content`

---

### 2.6 Switch Detection Mode

**Endpoint:** `PATCH /api/alert-rules/{rule_id}/detection-mode`  
**Description:** Switch detection mode for existing rule.

**Request:**
```bash
curl -X PATCH http://localhost:8001/api/alert-rules/{rule_id}/detection-mode \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "detection_type": "ml_model",
    "model_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
    "anomaly_threshold": 0.9
  }'
```

**Response:** `200 OK`
```json
{
  "success": true
}
```

---

## 3. ML Models Endpoints

### 3.1 List ML Models

**Endpoint:** `GET /api/ml-models`  
**Description:** Retrieve all ML models for authenticated user's company.

**Request:**
```bash
curl -X GET http://localhost:8001/api/ml-models \
  -H "Authorization: Bearer {token}"
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| status | string | No | Filter by: active, training, deprecated, failed |

**Response:** `200 OK`
```json
[
  {
    "model_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "model_name": "Pump Anomaly Detector v1",
    "description": "Isolation Forest model trained on 52 sensors",
    "model_type": "isolation_forest",
    "model_version": 1,
    "mlflow_run_id": "abc123def456",
    "mlflow_experiment_id": "exp001",
    "model_path": "runs:/abc123def456/model",
    "training_metrics": {
      "accuracy": 0.95,
      "precision": 0.93,
      "recall": 0.97,
      "f1_score": 0.95
    },
    "hyperparameters": {
      "n_estimators": 100,
      "contamination": 0.1
    },
    "feature_names": ["sensor_00", "sensor_01", "..."],
    "sensor_ids": ["uuid1", "uuid2", "..."],
    "accuracy": 0.95,
    "precision_score": 0.93,
    "recall": 0.97,
    "f1_score": 0.95,
    "auc_roc": 0.98,
    "training_samples": 10000,
    "training_start_date": "2025-11-01T00:00:00Z",
    "training_end_date": "2025-11-01T02:30:00Z",
    "status": "active",
    "deployed_at": "2025-11-01T03:00:00Z",
    "deprecated_at": null,
    "created_at": "2025-11-01T02:45:00Z",
    "updated_at": "2025-11-01T03:00:00Z",
    "created_by": "ml-training-service"
  }
]
```

---

### 3.2 Get Single ML Model

**Endpoint:** `GET /api/ml-models/{model_id}`  
**Description:** Retrieve specific ML model by ID.

**Request:**
```bash
curl -X GET http://localhost:8001/api/ml-models/a1b2c3d4-e5f6-7890-1234-567890abcdef \
  -H "Authorization: Bearer {token}"
```

**Response:** `200 OK` (same structure as list item)

---

### 3.3 Create ML Model

**Endpoint:** `POST /api/ml-models`  
**Description:** Register new ML model.

**Request:**
```bash
curl -X POST http://localhost:8001/api/ml-models \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "model_name": "Motor Vibration Detector",
    "description": "LSTM model for vibration anomaly detection",
    "model_type": "lstm",
    "model_version": 1,
    "model_path": "s3://models/motor_lstm_v1.pkl",
    "accuracy": 0.92,
    "status": "training"
  }'
```

**Required Fields:**
| Field | Type | Description |
|-------|------|-------------|
| model_name | string | Model name |
| model_type | enum | isolation_forest, lstm, autoencoder, statistical |
| status | enum | active, training, deprecated, failed |

**Response:** `201 Created` (created model object)

---

### 3.4 Train ML Model

**Endpoint:** `POST /api/ml-models/train`  
**Description:** Initiate ML model training (placeholder for Phase 2d).

**Request:**
```bash
curl -X POST http://localhost:8001/api/ml-models/train \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "model_type": "isolation_forest",
    "training_config": {
      "n_estimators": 100,
      "contamination": 0.1
    }
  }'
```

**Response:** `202 Accepted`
```json
{
  "status": "accepted",
  "message": "ML model training is planned for Phase 2d implementation",
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "config": {...}
}
```

---

### 3.5 Register Model from MLflow

**Endpoint:** `POST /api/ml-models/register-from-mlflow`  
**Description:** Register trained model from MLflow tracking server.

**Request:**
```bash
curl -X POST http://localhost:8001/api/ml-models/register-from-mlflow \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "mlflow_run_id": "abc123def456",
    "model_name": "Pump Anomaly Detector",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "description": "Production-ready model"
  }'
```

**Required Fields:**
| Field | Type | Description |
|-------|------|-------------|
| mlflow_run_id | string | MLflow run UUID |
| model_name | string | Human-readable model name |
| equipment_id | UUID | Target equipment |

**Response:** `201 Created` (registered model object with metrics from MLflow)

**Note:** Automatically extracts metrics, parameters, and artifacts from MLflow run.

---

### 3.6 Update Model Status

**Endpoint:** `PUT /api/ml-models/{model_id}/status`  
**Description:** Update model status (e.g., promote to production).

**Request:**
```bash
curl -X PUT http://localhost:8001/api/ml-models/{model_id}/status \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "active"
  }'
```

**Valid Statuses:**
- `active` - Available for use
- `training` - Currently training
- `deprecated` - Outdated, not recommended
- `failed` - Training or deployment failed

**Response:** `200 OK` (updated model object)

**Note:** Setting status to `production` automatically demotes other production models for same equipment to `active`.

---

## 4. Alerts History Endpoints

### 4.1 List Alerts

**Endpoint:** `GET /api/alerts`  
**Description:** Retrieve alert history with filtering.

**Request:**
```bash
curl -X GET "http://localhost:8001/api/alerts?severity=critical&limit=50" \
  -H "Authorization: Bearer {token}"
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| sensor_id | UUID | No | Filter by sensor |
| equipment_id | UUID | No | Filter by equipment |
| severity | enum | No | low, medium, high, critical |
| detection_type | enum | No | threshold, ml_model, hybrid |
| acknowledged | boolean | No | Filter by acknowledgment status |
| limit | integer | No | Max results (1-1000, default: 100) |

**Response:** `200 OK`
```json
[
  {
    "alert_id": "f1e2d3c4-b5a6-7890-1234-567890abcdef",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "rule_id": "7fd78efb-2587-4dc6-970d-fed95308663b",
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "site_id": null,
    "detection_type": "threshold",
    "threshold_value": 80.0,
    "actual_value": 95.3,
    "condition": "greater_than",
    "model_id": null,
    "anomaly_score": null,
    "severity": "high",
    "message": "Temperature exceeded threshold: 95.3°C > 80.0°C",
    "triggered_at": "2025-11-08T14:30:00Z",
    "acknowledged": false,
    "acknowledged_at": null,
    "acknowledged_by": null,
    "created_at": "2025-11-08T14:30:00.123Z"
  }
]
```

---

### 4.2 Get Unacknowledged Alerts

**Endpoint:** `GET /api/alerts/unacknowledged`  
**Description:** Retrieve only unacknowledged alerts.

**Request:**
```bash
curl -X GET "http://localhost:8001/api/alerts/unacknowledged?limit=100" \
  -H "Authorization: Bearer {token}"
```

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| limit | integer | No | Max results (1-1000, default: 100) |

**Response:** `200 OK` (array of alert objects, filtered to `acknowledged=false`)

---

### 4.3 Get Critical Alerts

**Endpoint:** `GET /api/alerts/critical`  
**Description:** Retrieve only critical severity alerts.

**Request:**
```bash
curl -X GET "http://localhost:8001/api/alerts/critical?limit=50" \
  -H "Authorization: Bearer {token}"
```

**Response:** `200 OK` (array of alert objects, filtered to `severity=critical`)

---

### 4.4 Acknowledge Alert

**Endpoint:** `POST /api/alerts/{alert_id}/acknowledge`  
**Description:** Mark alert as acknowledged.

**Request:**
```bash
curl -X POST http://localhost:8001/api/alerts/f1e2d3c4-b5a6-7890-1234-567890abcdef/acknowledge \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "acknowledged_by": "john.doe@acme.com"
  }'
```

**Required Fields:**
| Field | Type | Description |
|-------|------|-------------|
| acknowledged_by | string | User who acknowledged alert |

**Response:** `200 OK`
```json
{
  "success": true,
  "alert_id": "f1e2d3c4-b5a6-7890-1234-567890abcdef"
}
```

---

### 4.5 Get Alert Statistics

**Endpoint:** `GET /api/alerts/stats`  
**Description:** Retrieve aggregated alert statistics for company.

**Request:**
```bash
curl -X GET http://localhost:8001/api/alerts/stats \
  -H "Authorization: Bearer {token}"
```

**Response:** `200 OK`
```json
{
  "total_alerts": 1523,
  "by_severity": {
    "low": 450,
    "medium": 823,
    "high": 200,
    "critical": 50
  },
  "by_detection_type": {
    "threshold": 1200,
    "ml_model": 300,
    "hybrid": 23
  },
  "by_acknowledgment": {
    "acknowledged": 1400,
    "unacknowledged": 123
  }
}
```

---

## 5. Data Models

### 5.1 Alert Rule

```typescript
{
  rule_id: UUID,              // Auto-generated
  company_id: UUID,           // Injected from JWT
  name: string,               // Required, unique per company
  description: string?,       // Optional
  
  // Target Selection (at least one required)
  sensor_pattern: string?,    // Regex pattern
  sensor_id: UUID?,           // Specific sensor
  equipment_id: UUID?,        // All sensors on equipment
  site_id: string?,           // Site identifier
  
  // Detection Configuration
  detection_type: enum,       // threshold | ml_model | hybrid | disabled
  
  // Threshold Fields (required for threshold/hybrid)
  condition: enum?,           // greater_than | less_than | equals | not_equals | between | outside_range
  threshold: float?,          // Single threshold
  threshold_min: float?,      // Range lower bound
  threshold_max: float?,      // Range upper bound
  
  // ML Fields (required for ml_model/hybrid)
  model_id: UUID?,            // ML model reference
  anomaly_threshold: float?,  // 0.0-1.0, default: 0.85
  ml_config: object?,         // ML configuration JSON
  
  // Metadata
  severity: enum,             // low | medium | high | critical
  priority: integer,          // Default: 0
  enabled: boolean,           // Default: true
  created_at: timestamp,      // Auto-generated
  updated_at: timestamp,      // Auto-updated
  created_by: string?         // Optional creator ID
}
```

---

### 5.2 ML Model

```typescript
{
  model_id: UUID,                     // Auto-generated
  company_id: UUID,                   // Injected from JWT
  equipment_id: UUID?,                // Target equipment
  
  // Model Identity
  model_name: string,                 // Required
  description: string?,               // Optional
  model_type: enum,                   // isolation_forest | lstm | autoencoder | statistical
  model_version: integer,             // Default: 1
  
  // MLflow Integration
  mlflow_run_id: string?,             // MLflow run UUID
  mlflow_experiment_id: string?,      // MLflow experiment ID
  model_path: string?,                // Model artifact path
  
  // Training Configuration
  training_metrics: object?,          // Training metrics JSON
  hyperparameters: object?,           // Model hyperparameters JSON
  feature_names: string[]?,           // Input feature names
  sensor_ids: string[]?,              // Sensor UUIDs used
  
  // Performance Metrics
  accuracy: float?,                   // 0.0-1.0
  precision_score: float?,            // 0.0-1.0
  recall: float?,                     // 0.0-1.0
  f1_score: float?,                   // 0.0-1.0
  auc_roc: float?,                    // 0.0-1.0
  
  // Training Metadata
  training_samples: integer?,         // Sample count
  training_start_date: timestamp?,    // Training start
  training_end_date: timestamp?,      // Training end
  
  // Status
  status: enum,                       // active | training | deprecated | failed
  deployed_at: timestamp?,            // Production deployment time
  deprecated_at: timestamp?,          // Deprecation time
  created_at: timestamp,              // Auto-generated
  updated_at: timestamp,              // Auto-updated
  created_by: string?                 // Optional creator ID
}
```

---

### 5.3 Alert

```typescript
{
  alert_id: UUID,                // Auto-generated
  company_id: UUID,              // Injected from JWT
  rule_id: UUID?,                // Triggering rule
  
  // Context
  sensor_id: UUID?,              // Sensor that triggered
  equipment_id: UUID?,           // Equipment reference
  site_id: string?,              // Site identifier
  
  // Detection Details
  detection_type: enum,          // threshold | ml_model | hybrid
  
  // Threshold Detection (if applicable)
  threshold_value: float?,       // Expected threshold
  actual_value: float?,          // Measured value
  condition: string?,            // Condition that failed
  
  // ML Detection (if applicable)
  model_id: UUID?,               // ML model used
  anomaly_score: float?,         // Anomaly score (0.0-1.0)
  
  // Alert Data
  severity: enum,                // low | medium | high | critical
  message: string,               // Alert message
  triggered_at: timestamp,       // When alert triggered
  
  // Acknowledgment
  acknowledged: boolean,         // Default: false
  acknowledged_at: timestamp?,   // When acknowledged
  acknowledged_by: string?,      // Who acknowledged
  
  // Metadata
  created_at: timestamp          // Auto-generated
}
```

---

## 6. Error Responses

### 6.1 Standard Error Format

All errors return JSON with `detail` field:

```json
{
  "detail": "Error message description"
}
```

### 6.2 HTTP Status Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| 200 | OK | Successful GET, PUT, PATCH |
| 201 | Created | Successful POST |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Invalid input data, validation errors |
| 401 | Unauthorized | Missing or invalid JWT token |
| 403 | Forbidden | User lacks permission for resource |
| 404 | Not Found | Resource does not exist |
| 422 | Unprocessable Entity | Pydantic validation errors |
| 503 | Service Unavailable | Database connection failure |

### 6.3 Common Error Examples

**Invalid Token:**
```json
{
  "detail": "Could not validate credentials"
}
```

**Resource Not Found:**
```json
{
  "detail": "Rule not found"
}
```

**Validation Error:**
```json
{
  "detail": [
    {
      "loc": ["body", "severity"],
      "msg": "Input should be 'low', 'medium', 'high' or 'critical'",
      "type": "enum"
    }
  ]
}
```

**Database Error:**
```json
{
  "detail": "Failed to create rule: column 'invalid_column' does not exist"
}
```

---

## 7. Usage Examples

### 7.1 Complete Alert Rule Workflow

**Step 1:** Authenticate and obtain token
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@acme.com&password=SecurePass123!" \
  | jq -r '.access_token')
```

**Step 2:** Create threshold-based alert rule
```bash
curl -X POST http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "High Temperature Warning",
    "description": "Alert when temperature exceeds 85°C",
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "detection_type": "threshold",
    "condition": "greater_than",
    "threshold": 85.0,
    "severity": "high",
    "priority": 5,
    "enabled": true
  }'
```

**Step 3:** List all rules to verify creation
```bash
curl -X GET http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Step 4:** Update rule threshold
```bash
curl -X PUT http://localhost:8001/api/alert-rules/{rule_id} \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "threshold": 90.0,
    "description": "Updated threshold to 90°C"
  }'
```

**Step 5:** Check for triggered alerts
```bash
curl -X GET "http://localhost:8001/api/alerts?severity=high&acknowledged=false" \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Step 6:** Acknowledge alert
```bash
curl -X POST http://localhost:8001/api/alerts/{alert_id}/acknowledge \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "acknowledged_by": "test@acme.com"
  }'
```

---

### 7.2 ML Model Integration Workflow

**Step 1:** Register trained model from MLflow
```bash
curl -X POST http://localhost:8001/api/ml-models/register-from-mlflow \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mlflow_run_id": "abc123def456",
    "model_name": "Pump Vibration Detector",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "description": "Isolation Forest trained on 10k samples"
  }'
```

**Step 2:** Get model ID from response
```bash
MODEL_ID=$(curl -s -X GET http://localhost:8001/api/ml-models \
  -H "Authorization: Bearer $TOKEN" \
  | jq -r '.[0].model_id')
```

**Step 3:** Create ML-based alert rule
```bash
curl -X POST http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ML Anomaly Detection - Pump",
    "description": "Detects anomalies using trained ML model",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "detection_type": "ml_model",
    "model_id": "'$MODEL_ID'",
    "anomaly_threshold": 0.9,
    "severity": "critical",
    "priority": 10,
    "enabled": true
  }'
```

**Step 4:** Switch rule to hybrid mode (both threshold and ML)
```bash
curl -X PATCH http://localhost:8001/api/alert-rules/{rule_id}/detection-mode \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "detection_type": "hybrid",
    "condition": "greater_than",
    "threshold": 100.0,
    "model_id": "'$MODEL_ID'",
    "anomaly_threshold": 0.85
  }'
```

---

### 7.3 Monitoring Dashboard Workflow

**Get alert statistics:**
```bash
curl -X GET http://localhost:8001/api/alerts/stats \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Get recent critical alerts:**
```bash
curl -X GET "http://localhost:8001/api/alerts/critical?limit=10" \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Get unacknowledged alerts:**
```bash
curl -X GET "http://localhost:8001/api/alerts/unacknowledged?limit=50" \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Filter alerts by sensor:**
```bash
SENSOR_ID="550e8400-e29b-41d4-a716-446655440020"
curl -X GET "http://localhost:8001/api/alerts?sensor_id=$SENSOR_ID&limit=100" \
  -H "Authorization: Bearer $TOKEN" | jq
```

---

### 7.4 Batch Operations

**Create multiple rules:**
```bash
for sensor in sensor_01 sensor_02 sensor_03; do
  curl -X POST http://localhost:8001/api/alert-rules \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"High Temp - $sensor\",
      \"sensor_pattern\": \"$sensor\",
      \"detection_type\": \"threshold\",
      \"condition\": \"greater_than\",
      \"threshold\": 80.0,
      \"severity\": \"medium\",
      \"enabled\": true
    }"
done
```

**Disable all rules (get IDs first, then disable):**
```bash
curl -s -X GET http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer $TOKEN" \
  | jq -r '.[].rule_id' \
  | while read rule_id; do
      curl -X PUT http://localhost:8001/api/alert-rules/$rule_id \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"enabled": false}'
    done
```

---

## 8. Multi-Tenant Behavior

### 8.1 Tenant Isolation

All API requests are automatically scoped to the authenticated user's company:

1. JWT token contains user UUID
2. API queries database to find user's company UUID
3. All database queries use `SET search_path TO tenant_{company_uuid}, public`
4. Data is completely isolated at schema level

### 8.2 Cross-Tenant Access

Users **cannot** access data from other companies:
- All queries automatically filtered by tenant schema
- No company_id parameter needed in requests
- Attempting to access another tenant's resource returns 404 Not Found

### 8.3 Company ID in Responses

Although tenant isolation occurs at schema level, API responses include `company_id` for:
- Client-side caching
- Audit logging
- UI display purposes

---

## 9. Performance Considerations

### 9.1 Connection Pool

- Minimum connections: 5
- Maximum connections: 20
- Workers: 4 (uvicorn)
- Total max connections: 80 (4 workers × 20 connections)

### 9.2 Query Performance

**Schema Routing Overhead:**
- `SET search_path` per request: ~1ms
- Negligible compared to query execution time

**Recommended Practices:**
- Use pagination (limit parameter) for large result sets
- Filter by sensor_id or equipment_id when possible
- Index on frequently queried fields (severity, triggered_at, acknowledged)

### 9.3 Rate Limiting

Currently no rate limiting implemented. Consider implementing:
- Per-user limits: 100 requests/minute
- Per-company limits: 1000 requests/minute
- Burst allowance: 2× normal limit for 10 seconds

---

## 10. Health Check

**Endpoint:** `GET /health`

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "architecture": "schema-per-tenant"
}
```

**Endpoint:** `GET /`

**Response:**
```json
{
  "service": "IndustryFlow Alert Service API",
  "version": "2.0.0",
  "architecture": "schema-per-tenant",
  "endpoints": {
    "health": "/health",
    "alert_rules": "/api/alert-rules",
    "ml_models": "/api/ml-models",
    "alerts": "/api/alerts"
  }
}
```

---

**END OF DOCUMENTATION**
