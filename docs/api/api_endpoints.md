# API Gateway - API Reference & Usage Guide

**Component:** IndustryFlow API Gateway Service  
**API Version:** 1.0  
**Base URL:** `http://localhost:8000`  
**Date:** November 10, 2025

---

## 1. Authentication Endpoints

### 1.1 Login

**Endpoint:** `POST /auth/jwt/login`  
**Authorization:** None  
**Content-Type:** `application/x-www-form-urlencoded`

**Request Parameters:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| username | string | Yes | User email address |
| password | string | Yes | User password |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| access_token | string | JWT token (7-day expiration) |
| token_type | string | Always "bearer" |

**Status Codes:**
- `200 OK` - Login successful
- `400 Bad Request` - Invalid credentials
- `422 Unprocessable Entity` - Validation error

---

### 1.2 Register User

**Endpoint:** `POST /auth/register`  
**Authorization:** None  
**Content-Type:** `application/json`

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | Yes | User email address |
| password | string | Yes | Minimum 8 characters |
| company_id | UUID | Yes | Company UUID |
| role | enum | Yes | admin, engineer, observer |
| is_active | boolean | No | Default: true |
| is_superuser | boolean | No | Default: false |
| is_verified | boolean | No | Default: false |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | User identifier |
| email | string | User email |
| company_id | UUID | Assigned company |
| role | string | User role |
| is_active | boolean | Account status |
| is_superuser | boolean | Superuser flag |
| is_verified | boolean | Email verification status |

**Status Codes:**
- `201 Created` - User registered successfully
- `400 Bad Request` - Email already exists
- `422 Unprocessable Entity` - Validation error

---

### 1.3 Logout

**Endpoint:** `POST /auth/jwt/logout`  
**Authorization:** Bearer Token

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| detail | string | Logout confirmation message |

**Status Codes:**
- `200 OK` - Logout successful
- `401 Unauthorized` - Invalid or expired token

---

### 1.4 Get Current User

**Endpoint:** `GET /users/me`  
**Authorization:** Bearer Token

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | User identifier |
| email | string | User email |
| company_id | UUID | User's company |
| role | string | User role |
| is_active | boolean | Account status |
| is_superuser | boolean | Superuser flag |
| is_verified | boolean | Email verification status |

**Status Codes:**
- `200 OK` - User info retrieved
- `401 Unauthorized` - Invalid or expired token

---

### 1.5 List All Users (Admin)

**Endpoint:** `GET /api/users`  
**Authorization:** Bearer Token (admin role required)

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | User identifier |
| email | string | User email |
| company_id | UUID | User's company |
| role | string | User role |

**Status Codes:**
- `200 OK` - Users retrieved
- `401 Unauthorized` - Not authenticated
- `403 Forbidden` - Non-admin user

---

## 2. Measurement Endpoints

### 2.1 List Measurements

**Endpoint:** `GET /api/measurements`  
**Authorization:** Bearer Token

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| sensor_id | UUID | No | Filter by sensor |
| equipment_id | UUID | No | Filter by equipment |
| limit | integer | No | Max results (1-1000, default: 100) |

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| time | timestamp | Measurement timestamp |
| sensor_id | UUID | Sensor identifier |
| equipment_id | UUID | Equipment identifier |
| site_id | string | Site location |
| company_id | UUID | Company identifier (injected) |
| value | float | Measurement value |
| unit | string | Measurement unit |
| quality_code | integer | 0=good, 1=uncertain, 2=bad |

**Status Codes:**
- `200 OK` - Measurements retrieved (empty array if no data)
- `401 Unauthorized` - Not authenticated

---

### 2.2 Get Latest Measurements

**Endpoint:** `GET /api/measurements/latest`  
**Authorization:** Bearer Token

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| limit | integer | No | Max results (1-1000, default: 100) |

**Response:** Latest measurement per sensor (same fields as 2.1)

**Status Codes:**
- `200 OK` - Latest values retrieved
- `401 Unauthorized` - Not authenticated

---

### 2.3 Get Sensor Measurements

**Endpoint:** `GET /api/measurements/{sensor_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| sensor_id | UUID | Yes | Sensor identifier |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| limit | integer | No | Max results (1-1000, default: 100) |

**Response:** Sensor-specific measurements (same fields as 2.1)

**Status Codes:**
- `200 OK` - Measurements retrieved
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Sensor not found

---

## 3. Aggregation Endpoints

### 3.1 Get Aggregations

**Endpoint:** `GET /api/aggregations/{window}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| window | enum | Yes | 1min, 5min, 1hour |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| sensor_id | UUID | No | Filter by sensor |
| equipment_id | UUID | No | Filter by equipment |
| limit | integer | No | Max results (1-1000, default: 100) |

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| time | timestamp | Aggregation window start |
| sensor_id | UUID | Sensor identifier |
| equipment_id | UUID | Equipment identifier |
| site_id | string | Site location |
| company_id | UUID | Company identifier (injected) |
| avg_value | float | Average value in window |
| min_value | float | Minimum value in window |
| max_value | float | Maximum value in window |
| count_values | integer | Number of measurements |
| unit | string | Measurement unit |

**Status Codes:**
- `200 OK` - Aggregations retrieved
- `401 Unauthorized` - Not authenticated
- `422 Unprocessable Entity` - Invalid window parameter

---

### 3.2 Get Latest Aggregations

**Endpoint:** `GET /api/aggregations/{window}/latest`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| window | enum | Yes | 1min, 5min, 1hour |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| limit | integer | No | Max results (1-1000, default: 100) |

**Response:** Latest aggregation per sensor (same fields as 3.1)

**Status Codes:**
- `200 OK` - Latest aggregations retrieved
- `401 Unauthorized` - Not authenticated

---

### 3.3 Get Combined Aggregations

**Endpoint:** `GET /api/aggregations/combined/{sensor_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| sensor_id | UUID | Yes | Sensor identifier |

**Response:** All timeframe aggregations for specified sensor

**Status Codes:**
- `200 OK` - Combined aggregations retrieved
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Sensor not found

---

## 4. Equipment Management Endpoints

### 4.1 List Equipment

**Endpoint:** `GET /api/equipment`  
**Authorization:** Bearer Token

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| equipment_id | UUID | Equipment identifier |
| equipment_type | string | Equipment type |
| name | string | Equipment name |
| description | string | Description (nullable) |
| site_id | string | Site identifier (nullable) |
| location | string | Physical location |
| sensor_count | integer | Expected sensor count |
| batch_timeout_seconds | integer | Batch collection timeout |
| require_complete_batch | boolean | Require all sensors |
| min_sensors_for_partial | integer | Minimum for partial batch (nullable) |
| status | string | active, maintenance, decommissioned |
| commissioned_date | date | Commission date (nullable) |
| last_maintenance_date | date | Last maintenance (nullable) |
| next_maintenance_date | date | Next scheduled maintenance (nullable) |
| created_at | timestamp | Creation timestamp |
| updated_at | timestamp | Last update timestamp |
| created_by | string | Creator user (nullable) |
| expected_sensors | array | Expected sensor IDs |

**Status Codes:**
- `200 OK` - Equipment list retrieved
- `401 Unauthorized` - Not authenticated

---

### 4.2 Get Equipment

**Endpoint:** `GET /api/equipment/{equipment_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Response:** Single equipment object (same fields as 4.1)

**Status Codes:**
- `200 OK` - Equipment retrieved
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found

---

### 4.3 Create Equipment

**Endpoint:** `POST /api/equipment`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| equipment_id | UUID | No | Auto-generated if not provided |
| equipment_type | string | Yes | Equipment type |
| name | string | Yes | Equipment name |
| description | string | No | Description |
| site_id | string | No | Site identifier |
| location | string | Yes | Physical location |
| sensor_count | integer | Yes | Expected sensor count |
| batch_timeout_seconds | integer | No | Default: 5 |
| require_complete_batch | boolean | No | Default: true |
| min_sensors_for_partial | integer | No | Minimum sensors for partial batch |

**Response:** Created equipment object (same fields as 4.1)

**Status Codes:**
- `201 Created` - Equipment created
- `401 Unauthorized` - Not authenticated
- `409 Conflict` - Equipment ID already exists
- `422 Unprocessable Entity` - Validation error

---

### 4.4 Update Equipment

**Endpoint:** `PUT /api/equipment/{equipment_id}`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Request Fields:** All optional, only provided fields updated

| Field | Type | Description |
|-------|------|-------------|
| name | string | Equipment name |
| description | string | Description |
| location | string | Physical location |
| sensor_count | integer | Expected sensor count |
| status | string | active, maintenance, decommissioned |

**Response:** Updated equipment object (same fields as 4.1)

**Status Codes:**
- `200 OK` - Equipment updated
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found
- `422 Unprocessable Entity` - Validation error

---

### 4.5 Delete Equipment

**Endpoint:** `DELETE /api/equipment/{equipment_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Response:** No content

**Status Codes:**
- `204 No Content` - Equipment deleted
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found

---

### 4.6 List Equipment Sensors

**Endpoint:** `GET /api/equipment/{equipment_id}/sensors`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| sensor_id | UUID | Sensor identifier |
| equipment_id | UUID | Parent equipment ID |
| sensor_name | string | Sensor name |
| sensor_type | string | temperature, pressure, flow, etc |
| description | string | Description (nullable) |
| unit | string | Measurement unit |
| min_value | float | Minimum valid value (nullable) |
| max_value | float | Maximum valid value (nullable) |
| normal_min | float | Normal range minimum (nullable) |
| normal_max | float | Normal range maximum (nullable) |
| position | integer | Sensor position/order |
| is_critical | boolean | Critical sensor flag |
| is_required_for_ml | boolean | Required for ML models |
| status | string | active, maintenance, decommissioned |
| is_active | boolean | Active status |
| created_at | timestamp | Creation timestamp |
| updated_at | timestamp | Last update timestamp |

**Status Codes:**
- `200 OK` - Sensors retrieved
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found

---

### 4.7 Add Sensor to Equipment

**Endpoint:** `POST /api/equipment/{equipment_id}/sensors`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| sensor_id | UUID | No | Auto-generated if not provided |
| sensor_name | string | Yes | Sensor name |
| sensor_type | string | Yes | Sensor type |
| unit | string | Yes | Measurement unit |
| description | string | No | Description |
| position | integer | No | Sensor position |
| is_critical | boolean | No | Default: false |
| is_required_for_ml | boolean | No | Default: true |

**Response:** Created sensor object (same fields as 4.6)

**Status Codes:**
- `201 Created` - Sensor added
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found
- `409 Conflict` - Sensor ID already exists
- `422 Unprocessable Entity` - Validation error

---

### 4.8 Remove Sensor from Equipment

**Endpoint:** `DELETE /api/equipment/{equipment_id}/sensors/{sensor_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |
| sensor_id | UUID | Yes | Sensor identifier |

**Response:** No content

**Status Codes:**
- `204 No Content` - Sensor removed
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment or sensor not found

---

### 4.9 Bulk Add Sensors

**Endpoint:** `POST /api/equipment/{equipment_id}/sensors/bulk`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| sensors | array | Yes | Array of sensor objects |

**Sensor Object Fields:** Same as 4.7 (Add Sensor)

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| added_count | integer | Number of sensors added |
| sensors | array | Created sensor objects |

**Status Codes:**
- `201 Created` - Sensors added
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found
- `422 Unprocessable Entity` - Validation error

---

## 5. Alert Endpoints

### 5.1 List Alerts

**Endpoint:** `GET /api/alerts`  
**Authorization:** Bearer Token

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| sensor_id | UUID | No | Filter by sensor |
| equipment_id | UUID | No | Filter by equipment |
| severity | enum | No | info, low, medium, high, critical |
| detection_type | enum | No | threshold, ml_model, hybrid |
| acknowledged | boolean | No | Filter by acknowledgment |
| limit | integer | No | Max results (1-1000, default: 100) |

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| alert_id | UUID | Alert identifier |
| company_id | UUID | Company identifier (injected) |
| rule_id | UUID | Triggering rule ID |
| sensor_id | UUID | Sensor identifier |
| equipment_id | UUID | Equipment identifier |
| site_id | string | Site identifier (nullable) |
| detection_type | string | threshold, ml_model, hybrid |
| threshold_value | float | Threshold value (nullable) |
| actual_value | float | Measured value |
| condition | string | Condition evaluated (nullable) |
| model_id | UUID | ML model ID (nullable) |
| anomaly_score | float | ML anomaly score (nullable) |
| severity | string | info, low, medium, high, critical |
| message | string | Alert message |
| triggered_at | timestamp | Alert trigger time |
| acknowledged | boolean | Acknowledgment status |
| acknowledged_at | timestamp | Acknowledgment time (nullable) |
| acknowledged_by | string | User who acknowledged (nullable) |
| created_at | timestamp | Creation timestamp |

**Status Codes:**
- `200 OK` - Alerts retrieved (empty array if none)
- `401 Unauthorized` - Not authenticated

---

### 5.2 Get Unacknowledged Alerts

**Endpoint:** `GET /api/alerts/unacknowledged`  
**Authorization:** Bearer Token

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| limit | integer | No | Max results (1-1000, default: 100) |

**Response:** Alerts where `acknowledged = false` (same fields as 5.1)

**Status Codes:**
- `200 OK` - Unacknowledged alerts retrieved
- `401 Unauthorized` - Not authenticated

---

### 5.3 Get Critical Alerts

**Endpoint:** `GET /api/alerts/critical`  
**Authorization:** Bearer Token

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| limit | integer | No | Max results (1-1000, default: 100) |

**Response:** Alerts where `severity = critical` (same fields as 5.1)

**Status Codes:**
- `200 OK` - Critical alerts retrieved
- `401 Unauthorized` - Not authenticated

---

### 5.4 Acknowledge Alert

**Endpoint:** `PATCH /api/alerts/{alert_id}/acknowledge`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| alert_id | UUID | Yes | Alert identifier |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| message | string | Confirmation message |
| alert_id | UUID | Acknowledged alert ID |
| acknowledged_at | timestamp | Acknowledgment timestamp |
| acknowledged_by | string | User email |

**Status Codes:**
- `200 OK` - Alert acknowledged
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Alert not found or not owned by company

---

## 6. Alert Rules Endpoints

### 6.1 List Alert Rules

**Endpoint:** `GET /api/alert-rules`  
**Authorization:** Bearer Token

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| rule_id | UUID | Rule identifier |
| name | string | Rule name |
| description | string | Description (nullable) |
| sensor_id | UUID | Target sensor (nullable) |
| equipment_id | UUID | Target equipment (nullable) |
| sensor_pattern | string | Sensor name pattern (nullable) |
| site_id | string | Site filter (nullable) |
| detection_type | string | threshold, ml_anomaly, statistical |
| condition | string | Condition logic (nullable) |
| threshold | float | Threshold value (nullable) |
| threshold_min | float | Min threshold (nullable) |
| threshold_max | float | Max threshold (nullable) |
| model_id | UUID | ML model ID (nullable) |
| anomaly_threshold | float | Anomaly score threshold |
| model_config | json | Model configuration (nullable) |
| requires_complete_batch | boolean | Batch completeness requirement |
| min_batch_completeness | float | Minimum batch ratio (0.0-1.0) |
| severity | string | info, low, medium, high, critical |
| priority | integer | Rule priority (1-5) |
| enabled | boolean | Rule enabled status |
| created_at | timestamp | Creation timestamp |
| updated_at | timestamp | Last update timestamp |
| created_by | string | Creator user (nullable) |

**Status Codes:**
- `200 OK` - Rules retrieved
- `401 Unauthorized` - Not authenticated

---

### 6.2 Get Alert Rule

**Endpoint:** `GET /api/alert-rules/{rule_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| rule_id | UUID | Yes | Rule identifier |

**Response:** Single rule object (same fields as 6.1)

**Status Codes:**
- `200 OK` - Rule retrieved
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Rule not found or not owned by company

---

### 6.3 Create Alert Rule

**Endpoint:** `POST /api/alert-rules`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | Yes | Rule name |
| description | string | No | Description |
| sensor_id | UUID | No | Target sensor |
| equipment_id | UUID | No | Target equipment |
| detection_type | string | Yes | threshold, ml_anomaly, statistical |
| threshold | float | Conditional | Required for threshold detection |
| severity | string | Yes | info, low, medium, high, critical |
| enabled | boolean | No | Default: true |
| priority | integer | No | Default: 3 (1-5) |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| message | string | Confirmation message |
| rule | object | Created rule (same fields as 6.1) |

**Status Codes:**
- `201 Created` - Rule created
- `401 Unauthorized` - Not authenticated
- `422 Unprocessable Entity` - Validation error

---

### 6.4 Update Alert Rule

**Endpoint:** `PUT /api/alert-rules/{rule_id}`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| rule_id | UUID | Yes | Rule identifier |

**Request Fields:** All optional, only provided fields updated

| Field | Type | Description |
|-------|------|-------------|
| name | string | Rule name |
| threshold | float | Threshold value |
| severity | string | Severity level |
| enabled | boolean | Enable/disable rule |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| message | string | Confirmation message |
| rule | object | Updated rule (same fields as 6.1) |

**Status Codes:**
- `200 OK` - Rule updated
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Rule not found or not owned by company
- `422 Unprocessable Entity` - Validation error

---

### 6.5 Delete Alert Rule

**Endpoint:** `DELETE /api/alert-rules/{rule_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| rule_id | UUID | Yes | Rule identifier |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| message | string | Confirmation message |
| deleted_rule | object | Deleted rule summary |

**Status Codes:**
- `200 OK` - Rule deleted
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Rule not found or not owned by company

---

### 6.6 Switch Detection Mode

**Endpoint:** `PATCH /api/alert-rules/{rule_id}/detection-mode`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| rule_id | UUID | Yes | Rule identifier |

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| detection_type | string | Yes | threshold, ml_model, hybrid |
| model_id | UUID | Conditional | Required for ml_model |
| anomaly_threshold | float | Conditional | Required for ml_model |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| success | boolean | Operation status |

**Status Codes:**
- `200 OK` - Detection mode updated
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Rule not found
- `422 Unprocessable Entity` - Validation error

---

## 7. ML Models Endpoints

### 7.1 List ML Models

**Endpoint:** `GET /api/ml-models`  
**Authorization:** Bearer Token

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| status | string | No | active, training, deprecated, failed |

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| model_id | UUID | Model identifier |
| company_id | UUID | Company identifier (injected) |
| equipment_id | UUID | Target equipment |
| model_name | string | Model name |
| description | string | Description (nullable) |
| model_type | string | Model algorithm type |
| model_version | integer | Model version number |
| mlflow_run_id | string | MLflow run ID |
| mlflow_experiment_id | string | MLflow experiment ID |
| model_path | string | Model storage path |
| training_metrics | json | Training metrics |
| hyperparameters | json | Model hyperparameters |
| feature_names | array | Feature column names |
| sensor_ids | array | Training sensor IDs |
| accuracy | float | Model accuracy |
| precision_score | float | Precision metric |
| recall | float | Recall metric |
| f1_score | float | F1 score |
| auc_roc | float | AUC-ROC score |
| training_samples | integer | Number of training samples |
| training_start_date | timestamp | Training period start |
| training_end_date | timestamp | Training period end |
| status | string | active, training, deprecated, failed |
| deployed_at | timestamp | Deployment timestamp (nullable) |
| deprecated_at | timestamp | Deprecation timestamp (nullable) |
| created_at | timestamp | Creation timestamp |
| updated_at | timestamp | Last update timestamp |
| created_by | string | Creator user (nullable) |

**Status Codes:**
- `200 OK` - Models retrieved (empty array if none)
- `401 Unauthorized` - Not authenticated

---

### 7.2 Get ML Model

**Endpoint:** `GET /api/ml-models/{model_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| model_id | UUID | Yes | Model identifier |

**Response:** Single model object (same fields as 7.1)

**Status Codes:**
- `200 OK` - Model retrieved
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Model not found or not owned by company

---

### 7.3 Create ML Model Entry

**Endpoint:** `POST /api/ml-models`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| equipment_id | UUID | Yes | Target equipment |
| model_name | string | Yes | Model name |
| model_type | string | Yes | Algorithm type |
| description | string | No | Description |
| mlflow_run_id | string | No | MLflow run ID |
| model_path | string | No | Storage path |
| feature_names | array | No | Feature names |
| sensor_ids | array | No | Training sensors |

**Response:** Created model object (same fields as 7.1)

**Status Codes:**
- `201 Created` - Model entry created
- `401 Unauthorized` - Not authenticated
- `422 Unprocessable Entity` - Validation error

---

### 7.4 Update ML Model

**Endpoint:** `PUT /api/ml-models/{model_id}`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| model_id | UUID | Yes | Model identifier |

**Request Fields:** All optional, only provided fields updated

| Field | Type | Description |
|-------|------|-------------|
| model_name | string | Model name |
| description | string | Description |
| status | string | Model status |
| deployed_at | timestamp | Deployment timestamp |

**Response:** Updated model object (same fields as 7.1)

**Status Codes:**
- `200 OK` - Model updated
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Model not found
- `422 Unprocessable Entity` - Validation error

---

### 7.5 Delete ML Model

**Endpoint:** `DELETE /api/ml-models/{model_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| model_id | UUID | Yes | Model identifier |

**Response:** No content

**Status Codes:**
- `204 No Content` - Model deleted
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Model not found

---

### 7.6 Train Model (Placeholder)

**Endpoint:** `POST /api/ml-models/train`  
**Authorization:** Bearer Token  
**Content-Type:** `application/json`

**Request Fields:**

| Field | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Target equipment |
| model_type | string | Yes | Algorithm type |
| lookback_days | integer | No | Training data period |

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| message | string | Training initiated confirmation |
| job_id | UUID | Training job identifier |

**Status Codes:**
- `202 Accepted` - Training initiated
- `401 Unauthorized` - Not authenticated
- `422 Unprocessable Entity` - Validation error

**Note:** Actual training handled by ML Service

---

## 8. Training Data Endpoints

### 8.1 Get Training Data (JSON)

**Endpoint:** `GET /api/training-data/equipment/{equipment_id}`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| lookback_days | integer | No | Historical days (1-365, default: 30) |
| min_quality | float | No | Quality threshold (0.0-1.0, default: 0.8) |
| limit | integer | No | Max rows (1-100000, default: 10000) |

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| timestamp | timestamp | Measurement timestamp |
| sensor_name | string | Sensor identifier |
| value | float | Measurement value |

**Status Codes:**
- `200 OK` - Training data retrieved
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found
- `422 Unprocessable Entity` - Invalid parameters

---

### 8.2 Stream Training Data (CSV)

**Endpoint:** `GET /api/training-data/equipment/{equipment_id}/stream`  
**Authorization:** Bearer Token

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| lookback_days | integer | No | Historical days (1-365, default: 30) |
| min_quality | float | No | Quality threshold (0.0-1.0, default: 0.8) |

**Response:** CSV stream with chunked transfer encoding

**CSV Format:**
```
timestamp,sensor_name,value
2025-11-10T00:00:00Z,sensor_00,45.67
2025-11-10T00:00:01Z,sensor_01,23.45
```

**Status Codes:**
- `200 OK` - CSV stream initiated
- `401 Unauthorized` - Not authenticated
- `404 Not Found` - Equipment not found

---

## 9. Company Management Endpoints (Admin)

### 9.1 List Companies

**Endpoint:** `GET /api/companies`  
**Authorization:** Bearer Token (admin role required)

**Response Fields (Array):**

| Field | Type | Description |
|-------|------|-------------|
| company_id | UUID | Company identifier |
| company_name | string | Company name |
| is_active | boolean | Active status |
| created_at | timestamp | Creation timestamp |

**Status Codes:**
- `200 OK` - Companies retrieved
- `401 Unauthorized` - Not authenticated
- `403 Forbidden` - Non-admin user

---

### 9.2 Get Company

**Endpoint:** `GET /api/companies/{company_id}`  
**Authorization:** Bearer Token (admin role required)

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| company_id | UUID | Yes | Company identifier |

**Response:** Single company object (same fields as 9.1)

**Status Codes:**
- `200 OK` - Company retrieved
- `401 Unauthorized` - Not authenticated
- `403 Forbidden` - Non-admin user
- `404 Not Found` - Company not found

---

### 9.3 Create Company

**Endpoint:** `POST /api/companies`  
**Authorization:** Bearer Token (admin role required)  
**Content-Type:** `application/json`

**Request Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| company_id | UUID | No | Auto-generated if not provided |
| company_name | string | Yes | Company name |
| is_active | boolean | No | Default: true |

**Response:** Created company object (same fields as 9.1)

**Status Codes:**
- `201 Created` - Company created
- `401 Unauthorized` - Not authenticated
- `403 Forbidden` - Non-admin user
- `409 Conflict` - Company already exists
- `422 Unprocessable Entity` - Validation error

---

### 9.4 Update Company

**Endpoint:** `PUT /api/companies/{company_id}`  
**Authorization:** Bearer Token (admin role required)  
**Content-Type:** `application/json`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| company_id | UUID | Yes | Company identifier |

**Request Fields:** All optional

| Field | Type | Description |
|-------|------|-------------|
| company_name | string | Company name |
| is_active | boolean | Active status |

**Response:** Updated company object (same fields as 9.1)

**Status Codes:**
- `200 OK` - Company updated
- `401 Unauthorized` - Not authenticated
- `403 Forbidden` - Non-admin user
- `404 Not Found` - Company not found
- `422 Unprocessable Entity` - Validation error

---

### 9.5 Delete Company

**Endpoint:** `DELETE /api/companies/{company_id}`  
**Authorization:** Bearer Token (admin role required)

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| company_id | UUID | Yes | Company identifier |

**Response:** No content

**Status Codes:**
- `204 No Content` - Company deleted
- `401 Unauthorized` - Not authenticated
- `403 Forbidden` - Non-admin user
- `404 Not Found` - Company not found

---

## 10. WebSocket Endpoints

### 10.1 All Sensors Stream

**Endpoint:** `ws://localhost:8000/ws/sensors?token={jwt}`  
**Protocol:** WebSocket

**Connection:** Include JWT token in query parameter

**Message Interval:** 1 second

**Message Format:**

| Field | Type | Description |
|-------|------|-------------|
| type | string | Always "sensor_update" |
| timestamp | float | Unix timestamp |
| sensors | object | Sensor data dictionary |
| count | integer | Number of sensors |

**Sensor Data Fields:**

| Field | Type | Description |
|-------|------|-------------|
| value | float | Current sensor value |
| timestamp | string | ISO 8601 timestamp |
| equipment_id | UUID | Equipment identifier |
| company_id | UUID | Company identifier |
| unit | string | Measurement unit |
| quality_code | integer | Quality indicator |

**Disconnection:** Automatic on invalid/expired token

---

### 10.2 Equipment Sensors Stream

**Endpoint:** `ws://localhost:8000/ws/sensors/{equipment_id}?token={jwt}`  
**Protocol:** WebSocket

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| equipment_id | UUID | Yes | Equipment identifier |

**Message Format:** Same as 10.1, filtered to specified equipment

---

## 11. Cache Endpoints

### 11.1 Get Cached Sensors

**Endpoint:** `GET /api/cache/sensors`  
**Authorization:** Bearer Token

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| cached_sensors | integer | Number of cached sensors |
| company_id | UUID | Requestor's company |
| sensors | object | Sensor data dictionary |

**Sensor Data Fields:** Same as WebSocket (section 10.1)

**Status Codes:**
- `200 OK` - Cache retrieved (empty if no streaming data)
- `401 Unauthorized` - Not authenticated

---

## 12. System Endpoints

### 12.1 API Root

**Endpoint:** `GET /`  
**Authorization:** None

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| name | string | API name |
| version | string | API version |
| description | string | API description |
| docs_url | string | Swagger UI path |
| architecture | string | Architecture version |
| endpoints | object | Endpoint reference map |

**Status Codes:**
- `200 OK` - API info retrieved

---

### 12.2 Health Check

**Endpoint:** `GET /health`  
**Authorization:** None

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| status | string | healthy or unhealthy |
| database | string | connected or error |
| timestamp | string | ISO 8601 timestamp |

**Status Codes:**
- `200 OK` - Service healthy
- `503 Service Unavailable` - Service unhealthy

---

### 12.3 API Documentation

**Endpoint:** `GET /docs`  
**Authorization:** None  
**Description:** Swagger UI interactive documentation

**Endpoint:** `GET /redoc`  
**Authorization:** None  
**Description:** ReDoc alternative documentation

---

## 13. Error Response Format

All error responses follow consistent structure:

**Error Fields:**

| Field | Type | Description |
|-------|------|-------------|
| detail | string or object | Error description |

**Common Status Codes:**

| Code | Meaning | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 201 | Created | Resource created successfully |
| 202 | Accepted | Request accepted for processing |
| 204 | No Content | Success with no response body |
| 400 | Bad Request | Invalid request format |
| 401 | Unauthorized | Missing or invalid authentication |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 409 | Conflict | Resource already exists |
| 422 | Unprocessable Entity | Validation error |
| 500 | Internal Server Error | Server error |
| 503 | Service Unavailable | Service temporarily unavailable |

---

## 14. Related Services

### 14.1 Ingestion Service

Sensor data ingestion handled by separate service.

**Base URL:** `http://localhost:8003`  
**Endpoint:** `POST /ingest`  
**Purpose:** High-throughput sensor data ingestion  
**Reference:** See Ingestion Service documentation

---

**END OF DOCUMENT**