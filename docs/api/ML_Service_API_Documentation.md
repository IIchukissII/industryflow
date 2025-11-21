# ML Service API Documentation

**Service:** ML Service API
**Version:** 2.0.0
**Port:** 8002
**Architecture:** Schema-per-tenant (v5.0)
**Date:** November 2025

---

## Table of Contents

1. [Overview](#1-overview)
2. [Authentication](#2-authentication)
3. [Models API Endpoints](#3-models-api-endpoints)
4. [MLflow API Endpoints](#4-mlflow-api-endpoints)
5. [Request/Response Schemas](#5-requestresponse-schemas)
6. [Error Handling](#6-error-handling)
7. [Database Queries](#7-database-queries)

---

## 1. Overview

### 1.1 Service Information

- **Base URL:** `http://localhost:8002`
- **Protocol:** HTTP/REST
- **Authentication:** JWT Bearer Token
- **Content-Type:** `application/json`
- **Port:** 8002

### 1.2 Architecture Pattern

```
[Client Request + JWT]
        ↓
[FastAPI Application]
        ↓
[JWT Verification] → Extract user_id
        ↓
[Company ID Resolution] → Query tenant schemas for user
        ↓
[Schema Routing] → SET search_path TO tenant_{uuid}
        ↓
[Repository Layer] → Execute queries in tenant schema
        ↓
[Response + company_id injection]
```

### 1.3 Database Connections

**Dual Connection Pool Architecture:**

1. **IndustryFlow Database Pool:**
   - Database: `industryflow`
   - User: `ml_service_user`
   - Connection Limit: 10
   - Pool Size: 5-20 connections
   - Timeout: 60 seconds
   - Tables: `ml_models`, `equipment`, `sensors`

2. **MLflow Database Pool:**
   - Database: `mlflow`
   - User: `mlflow_user`
   - Connection Limit: 40
   - Pool Size: 5-20 connections
   - Timeout: 60 seconds
   - Tables: `experiments`, `runs`, `metrics`, `params`, `registered_models`

---

## 2. Authentication

### 2.1 JWT Token Verification

**Method:** `verify_jwt_token()`

**Input:**
```
Authorization: Bearer <token>
```

**Algorithm:**
1. Extract token from `Authorization` header
2. Validate header format: `Bearer <token>`
3. Decode JWT using `HS256` algorithm
4. Extract `sub` claim (user_id)
5. Return user information dict

**Output:**
```python
{
    "user_id": "uuid-string",
    "payload": {...}  # Full JWT payload
}
```

**Errors:**
- 401: Missing or invalid authorization header
- 401: Invalid or expired token
- 401: Invalid token payload (missing user_id)

### 2.2 Company ID Resolution

**Method:** `get_company_id_dependency()`

**Algorithm:**
```python
1. Get user_id from JWT token
2. Query information_schema for all tenant schemas:
   SELECT schema_name 
   FROM information_schema.schemata 
   WHERE schema_name LIKE 'tenant_%'
   
3. For each tenant schema:
   a. SET search_path TO {schema_name}, public
   b. SELECT company_id FROM "user" WHERE id = user_id
   c. If found, return company_id
   
4. If not found in any schema:
   Raise 404 error
```

**Output:**
```python
"550e8400-e29b-41d4-a716-446655440000"  # UUID string
```

**Errors:**
- 404: User not found in any tenant schema
- 500: Database pool not available

---

## 3. Models API Endpoints

### 3.1 List Models

**Endpoint:** `GET /api/models`

**Authentication:** Required (JWT)

**Query Parameters:**
- `status` (optional): Filter by status (production, staging, archived, active)
- `limit` (optional, default=50): Maximum number of results

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Normalize company_id to schema name:
   tenant_{uuid_with_underscores}
3. Set search_path to tenant schema
4. Execute query with optional status filter:
   SELECT * FROM ml_models 
   WHERE status = ? (if provided)
   ORDER BY created_at DESC 
   LIMIT ?
5. Inject company_id into each result
6. Parse JSON fields (training_metrics, hyperparameters)
7. Return ModelListResponse
```

**Response Schema:**
```json
{
  "total": 0,
  "models": [
    {
      "model_id": "uuid",
      "company_id": "uuid",
      "equipment_id": "uuid",
      "model_name": "string",
      "description": "string",
      "model_type": "string",
      "model_version": 1,
      "mlflow_run_id": "string",
      "mlflow_experiment_id": "string",
      "model_path": "s3://path",
      "training_metrics": {
        "loss": 0.05,
        "val_loss": 0.07
      },
      "hyperparameters": {
        "learning_rate": 0.001,
        "batch_size": 32
      },
      "feature_names": ["sensor_1", "sensor_2"],
      "sensor_ids": ["uuid1", "uuid2"],
      "accuracy": 0.95,
      "precision_score": 0.94,
      "recall": 0.96,
      "f1_score": 0.95,
      "auc_roc": 0.97,
      "training_samples": 10000,
      "training_start_date": "2025-11-01T00:00:00Z",
      "training_end_date": "2025-11-01T02:30:00Z",
      "status": "production",
      "deployed_at": "2025-11-01T03:00:00Z",
      "created_at": "2025-11-01T00:00:00Z"
    }
  ]
}
```

**Status Codes:**
- 200: Success
- 401: Unauthorized (invalid/missing token)
- 404: User not found
- 500: Internal server error

### 3.2 Get Model by ID

**Endpoint:** `GET /api/models/{model_id}`

**Authentication:** Required (JWT)

**Path Parameters:**
- `model_id`: UUID of the model

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema
3. Query single model:
   SELECT * FROM ml_models WHERE model_id = ?
4. If not found, return 404
5. Parse JSON fields
6. Inject company_id
7. Return model data
```

**Response Schema:** Same as single model in list response

**Status Codes:**
- 200: Success
- 404: Model not found
- 401: Unauthorized

### 3.3 Deploy Model

**Endpoint:** `POST /api/models/{model_id}/deploy`

**Authentication:** Required (JWT)

**Path Parameters:**
- `model_id`: UUID of the model

**Request Body:**
```json
{
  "model_id": "uuid",
  "environment": "production"
}
```

**Valid Environments:**
- `production`
- `staging`
- `archived`
- `active`

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Validate environment value
3. Set search_path to tenant schema
4. Check if model exists:
   SELECT 1 FROM ml_models WHERE model_id = ?
5. If not found, return 404
6. Update status:
   UPDATE ml_models SET status = ? WHERE model_id = ?
7. Return success response
```

**Response Schema:**
```json
{
  "status": "success",
  "model_id": "uuid",
  "environment": "production",
  "updated_at": "2025-11-08T19:00:00.000000"
}
```

**Status Codes:**
- 200: Success
- 400: Invalid environment value
- 404: Model not found
- 500: Update failed

### 3.4 Delete Model (Archive)

**Endpoint:** `DELETE /api/models/{model_id}`

**Authentication:** Required (JWT)

**Path Parameters:**
- `model_id`: UUID of the model

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema
3. Check if model exists
4. Soft delete (set status to archived):
   UPDATE ml_models SET status = 'archived' WHERE model_id = ?
5. Return success response
```

**Response Schema:**
```json
{
  "status": "archived",
  "model_id": "uuid"
}
```

**Status Codes:**
- 200: Success
- 404: Model not found
- 500: Archive failed

### 3.5 Get Latest Model

**Endpoint:** `GET /api/models/latest/{model_type}`

**Authentication:** Required (JWT)

**Path Parameters:**
- `model_type`: Type of model (e.g., "isolation_forest", "random_forest")

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema
3. Query latest active model:
   SELECT model_id, model_name, model_type, model_path, accuracy, created_at
   FROM ml_models
   WHERE status IN ('active', 'production')
     AND model_type = ?
   ORDER BY created_at DESC
   LIMIT 1
4. If not found, return 404
5. Inject company_id and return
```

**Response Schema:**
```json
{
  "model_id": "uuid",
  "company_id": "uuid",
  "model_name": "string",
  "model_type": "string",
  "model_path": "s3://path",
  "accuracy": 0.95,
  "created_at": "2025-11-01T00:00:00Z"
}
```

**Status Codes:**
- 200: Success
- 404: No active model found for type

### 3.6 Compare Models

**Endpoint:** `POST /api/models/compare`

**Authentication:** Required (JWT)

**Request Body:**
```json
["uuid1", "uuid2", "uuid3"]
```

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema
3. Query multiple models:
   SELECT model_id, model_name, model_type, accuracy,
          precision_score, recall, f1_score, 
          training_start_date, model_version
   FROM ml_models
   WHERE model_id = ANY(?)
   ORDER BY created_at DESC
4. Return comparison data
```

**Response Schema:**
```json
{
  "models_compared": 3,
  "comparison": [
    {
      "model_id": "uuid",
      "company_id": "uuid",
      "model_name": "string",
      "model_type": "string",
      "accuracy": 0.95,
      "precision_score": 0.94,
      "recall": 0.96,
      "f1_score": 0.95,
      "training_start_date": "2025-11-01T00:00:00Z",
      "model_version": 1
    }
  ]
}
```

**Status Codes:**
- 200: Success
- 401: Unauthorized

### 3.7 Download Model

**Endpoint:** `GET /api/models/{model_id}/download`

**Authentication:** Required (JWT)

**Path Parameters:**
- `model_id`: UUID of the model

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema
3. Query model path and metadata
4. Return download information
```

**Response Schema:**
```json
{
  "model_id": "uuid",
  "model_name": "string",
  "model_path": "s3://mlflow/models/...",
  "download_info": "Model stored in MLflow/MinIO. Use MLflow client to download.",
  "mlflow_run_id": "string"
}
```

**Status Codes:**
- 200: Success
- 404: Model not found

---

## 4. MLflow API Endpoints

### 4.1 List Experiments

**Endpoint:** `GET /api/mlflow/experiments`

**Authentication:** Required (JWT)

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema in mlflow database
3. Query experiments:
   SELECT experiment_id, name, artifact_location, lifecycle_stage
   FROM experiments
   WHERE lifecycle_stage = 'active'
   ORDER BY experiment_id DESC
4. Return experiment list
```

**Response Schema:**
```json
{
  "total": 1,
  "experiments": [
    {
      "experiment_id": 0,
      "name": "Default",
      "artifact_location": "s3://mlflow/0",
      "lifecycle_stage": "active"
    }
  ]
}
```

**Status Codes:**
- 200: Success
- 401: Unauthorized

### 4.2 Get Experiment Details

**Endpoint:** `GET /api/mlflow/experiments/{experiment_id}`

**Authentication:** Required (JWT)

**Path Parameters:**
- `experiment_id`: Experiment ID (integer)

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema in mlflow database
3. Query experiment:
   SELECT experiment_id, name, artifact_location, lifecycle_stage,
          creation_time, last_update_time
   FROM experiments
   WHERE experiment_id = ?
4. If not found, return 404
5. Return experiment details
```

**Response Schema:**
```json
{
  "experiment_id": 0,
  "name": "Default",
  "artifact_location": "s3://mlflow/0",
  "lifecycle_stage": "active",
  "creation_time": 1699468800000,
  "last_update_time": 1699468800000
}
```

**Status Codes:**
- 200: Success
- 404: Experiment not found

### 4.3 List Runs for Experiment

**Endpoint:** `GET /api/mlflow/experiments/{experiment_id}/runs`

**Authentication:** Required (JWT)

**Path Parameters:**
- `experiment_id`: Experiment ID (integer)

**Query Parameters:**
- `max_results` (optional, default=100): Maximum number of results

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema in mlflow database
3. Verify experiment exists
4. Query runs:
   SELECT run_uuid as run_id, experiment_id, status,
          start_time, end_time, artifact_uri
   FROM runs
   WHERE experiment_id = ?
   ORDER BY start_time DESC
   LIMIT ?
5. Return runs list
```

**Response Schema:**
```json
{
  "total": 2,
  "runs": [
    {
      "run_id": "uuid",
      "experiment_id": "0",
      "status": "FINISHED",
      "start_time": 1699468800000,
      "end_time": 1699472400000,
      "artifact_uri": "s3://mlflow/0/uuid/artifacts"
    }
  ]
}
```

**Status Codes:**
- 200: Success
- 404: Experiment not found

### 4.4 Get Run Details

**Endpoint:** `GET /api/mlflow/runs/{run_id}`

**Authentication:** Required (JWT)

**Path Parameters:**
- `run_id`: Run UUID

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema in mlflow database
3. Query run info:
   SELECT run_uuid, experiment_id, status, start_time, 
          end_time, artifact_uri, lifecycle_stage
   FROM runs WHERE run_uuid = ?
4. Query metrics:
   SELECT key, value, timestamp, step 
   FROM metrics WHERE run_uuid = ?
5. Query params:
   SELECT key, value FROM params WHERE run_uuid = ?
6. Query tags:
   SELECT key, value FROM tags WHERE run_uuid = ?
7. Combine and return
```

**Response Schema:**
```json
{
  "run_id": "uuid",
  "experiment_id": "0",
  "status": "FINISHED",
  "start_time": 1699468800000,
  "end_time": 1699472400000,
  "artifact_uri": "s3://mlflow/0/uuid/artifacts",
  "lifecycle_stage": "active",
  "metrics": {
    "loss": 0.05,
    "accuracy": 0.95
  },
  "params": {
    "learning_rate": "0.001",
    "batch_size": "32"
  },
  "tags": {
    "mlflow.user": "ml_service",
    "mlflow.source.type": "LOCAL"
  }
}
```

**Status Codes:**
- 200: Success
- 404: Run not found

### 4.5 Get Run Metrics

**Endpoint:** `GET /api/mlflow/runs/{run_id}/metrics`

**Authentication:** Required (JWT)

**Path Parameters:**
- `run_id`: Run UUID

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Get run details (includes metrics)
3. Return metrics only
```

**Response Schema:**
```json
{
  "run_id": "uuid",
  "metrics": {
    "loss": 0.05,
    "val_loss": 0.07,
    "accuracy": 0.95,
    "val_accuracy": 0.93
  }
}
```

**Status Codes:**
- 200: Success
- 404: Run not found

### 4.6 List Registered Models

**Endpoint:** `GET /api/mlflow/models`

**Authentication:** Required (JWT)

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema in mlflow database
3. Query registered models:
   SELECT name, creation_timestamp, last_updated_timestamp, description
   FROM registered_models
   ORDER BY last_updated_timestamp DESC
4. Return models list
```

**Response Schema:**
```json
{
  "total": 1,
  "models": [
    {
      "name": "pump_anomaly_detector",
      "creation_timestamp": 1699468800000,
      "last_updated_timestamp": 1699472400000,
      "description": "Isolation Forest for pump anomaly detection"
    }
  ]
}
```

**Status Codes:**
- 200: Success

### 4.7 Get Registered Model with Versions

**Endpoint:** `GET /api/mlflow/models/{model_name}`

**Authentication:** Required (JWT)

**Path Parameters:**
- `model_name`: Name of the registered model

**Algorithm:**
```python
1. Verify JWT and resolve company_id
2. Set search_path to tenant schema in mlflow database
3. Query model:
   SELECT name, creation_timestamp, last_updated_timestamp, description
   FROM registered_models WHERE name = ?
4. Query versions:
   SELECT version, current_stage, run_id, status, creation_timestamp
   FROM model_versions WHERE name = ?
   ORDER BY version DESC
5. Combine and return
```

**Response Schema:**
```json
{
  "name": "pump_anomaly_detector",
  "creation_timestamp": 1699468800000,
  "last_updated_timestamp": 1699472400000,
  "description": "Isolation Forest for pump anomaly detection",
  "versions": [
    {
      "version": 2,
      "stage": "Production",
      "run_id": "uuid",
      "status": "READY",
      "creation_timestamp": 1699472400000
    },
    {
      "version": 1,
      "stage": "Archived",
      "run_id": "uuid",
      "status": "READY",
      "creation_timestamp": 1699468800000
    }
  ]
}
```

**Status Codes:**
- 200: Success
- 404: Model not found

### 4.8 Get MLflow UI URL

**Endpoint:** `GET /api/mlflow/ui`

**Authentication:** Not required

**Response Schema:**
```json
{
  "ui_url": "http://mlflow:5000",
  "message": "MLflow UI is for administrators only. Users access experiments via API."
}
```

**Status Codes:**
- 200: Success

### 4.9 MLflow Health Check

**Endpoint:** `GET /api/mlflow/health`

**Authentication:** Not required

**Algorithm:**
```python
1. Acquire connection from mlflow pool
2. Query total experiments:
   SELECT COUNT(*) FROM public.experiments
3. Return health status
```

**Response Schema:**
```json
{
  "status": "healthy",
  "database": "mlflow",
  "experiments_total": 5
}
```

**Status Codes:**
- 200: Success (even if unhealthy status)

---

## 5. Request/Response Schemas

### 5.1 ModelMetadata

```python
{
    "model_id": "uuid",                           # Primary key
    "company_id": "uuid",                         # Injected, not in database
    "equipment_id": "uuid | null",                # Foreign key to equipment
    "model_name": "string",                       # Model identifier
    "description": "string | null",               # Optional description
    "model_type": "string",                       # isolation_forest, random_forest, etc.
    "model_version": integer,                     # Version number
    "mlflow_run_id": "string | null",            # MLflow tracking
    "mlflow_experiment_id": "string | null",     # MLflow tracking
    "model_path": "string | null",               # S3/MinIO path
    "training_metrics": {                         # JSONB field
        "loss": float,
        "val_loss": float,
        "epoch_time": float
    },
    "hyperparameters": {                          # JSONB field
        "learning_rate": float,
        "batch_size": integer,
        "n_estimators": integer
    },
    "feature_names": ["string"],                  # Array of feature names
    "sensor_ids": ["uuid"],                       # Array of sensor UUIDs
    "accuracy": float,                            # 0.0 - 1.0
    "precision_score": float,                     # 0.0 - 1.0
    "recall": float,                              # 0.0 - 1.0
    "f1_score": float,                            # 0.0 - 1.0
    "auc_roc": float,                             # 0.0 - 1.0
    "training_samples": integer,                  # Number of samples used
    "training_start_date": "timestamp",           # ISO 8601 format
    "training_end_date": "timestamp",             # ISO 8601 format
    "status": "string",                           # production, staging, archived, active
    "deployed_at": "timestamp | null",            # When deployed
    "deprecated_at": "timestamp | null",          # When deprecated
    "created_at": "timestamp",                    # Creation timestamp
    "updated_at": "timestamp"                     # Last update timestamp
}
```

### 5.2 ModelDeployRequest

```python
{
    "model_id": "uuid",
    "environment": "production" | "staging" | "archived" | "active"
}
```

### 5.3 Error Response

```python
{
    "detail": "Error message string"
}
```

---

## 6. Error Handling

### 6.1 HTTP Status Codes

- **200 OK:** Request successful
- **201 Created:** Resource created
- **204 No Content:** Resource deleted
- **400 Bad Request:** Invalid input data
- **401 Unauthorized:** Missing or invalid authentication
- **404 Not Found:** Resource not found
- **500 Internal Server Error:** Server error

### 6.2 Error Response Format

All errors return JSON:
```json
{
  "detail": "Descriptive error message"
}
```

### 6.3 Common Errors

**Authentication Errors:**
```json
{"detail": "Missing or invalid authorization header"}
{"detail": "Invalid or expired token"}
{"detail": "User not found or not associated with any company"}
```

**Resource Errors:**
```json
{"detail": "Model not found"}
{"detail": "Experiment not found"}
{"detail": "Run not found"}
```

**Validation Errors:**
```json
{"detail": "Invalid environment. Must be one of: production, staging, archived, active"}
```

---

## 7. Database Queries

### 7.1 Schema Routing Pattern

**All queries follow this pattern:**

```python
# 1. Normalize company_id to schema name
schema_name = f"tenant_{company_id.replace('-', '_')}"

# 2. Set search path
await conn.execute(f"SET search_path TO {schema_name}, public")

# 3. Execute query (no company_id in WHERE clause)
rows = await conn.fetch("SELECT * FROM ml_models WHERE status = $1", status)

# 4. Inject company_id into response
return [{**dict(row), 'company_id': company_id} for row in rows]
```

### 7.2 Company ID Resolution Query

```sql
-- Get all tenant schemas
SELECT schema_name 
FROM information_schema.schemata 
WHERE schema_name LIKE 'tenant_%'
ORDER BY schema_name;

-- For each schema, check if user exists
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;
SELECT company_id FROM "user" WHERE id = $1;
```

### 7.3 Model Queries

**List models:**
```sql
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;

SELECT 
    model_id, equipment_id, model_name, description, model_type,
    model_version, mlflow_run_id, mlflow_experiment_id, model_path,
    training_metrics, hyperparameters, feature_names, sensor_ids,
    accuracy, precision_score, recall, f1_score, auc_roc,
    training_samples, training_start_date, training_end_date,
    status, deployed_at, deprecated_at, created_at, updated_at
FROM ml_models
WHERE status = $1  -- Optional filter
ORDER BY created_at DESC
LIMIT $2;
```

**Get model by ID:**
```sql
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;

SELECT * FROM ml_models WHERE model_id = $1;
```

**Update model status:**
```sql
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;

UPDATE ml_models 
SET status = $1 
WHERE model_id = $2;
```

**Get latest model:**
```sql
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;

SELECT model_id, model_name, model_type, model_path, accuracy, created_at
FROM ml_models
WHERE status IN ('active', 'production')
  AND model_type = $1
ORDER BY created_at DESC
LIMIT 1;
```

### 7.4 MLflow Queries

**List experiments:**
```sql
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;

SELECT experiment_id, name, artifact_location, lifecycle_stage
FROM experiments
WHERE lifecycle_stage = 'active'
ORDER BY experiment_id DESC;
```

**Get run with metrics:**
```sql
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;

-- Run info
SELECT run_uuid as run_id, experiment_id, status, start_time, 
       end_time, artifact_uri, lifecycle_stage
FROM runs 
WHERE run_uuid = $1;

-- Metrics
SELECT key, value, timestamp, step
FROM metrics
WHERE run_uuid = $1
ORDER BY timestamp;

-- Params
SELECT key, value 
FROM params 
WHERE run_uuid = $1;

-- Tags
SELECT key, value 
FROM tags 
WHERE run_uuid = $1;
```

### 7.5 Connection Pool Management

**IndustryFlow Pool:**
```python
db_pool = await asyncpg.create_pool(
    host='timescaledb',
    port=5432,
    database='industryflow',
    user='ml_service_user',
    password='***',
    min_size=5,
    max_size=20,
    command_timeout=60
)
```

**MLflow Pool:**
```python
mlflow_pool = await asyncpg.create_pool(
    host='timescaledb',
    port=5432,
    database='mlflow',
    user='mlflow_user',
    password='***',
    min_size=5,
    max_size=20,
    command_timeout=60
)
```

**Query Execution:**
```python
async with pool.acquire() as conn:
    await conn.execute(f"SET search_path TO {schema_name}, public")
    rows = await conn.fetch(query, *params)
```

---

## End of API Documentation

**Key Principles:**
1. All queries use schema-per-tenant isolation
2. Company ID injected in response, never in database
3. JWT authentication required for all protected endpoints
4. Dual connection pools for industryflow and mlflow databases
5. JSONB fields parsed from string if necessary
6. All timestamps in ISO 8601 format
