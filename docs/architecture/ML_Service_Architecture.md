<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ML Service Architecture Documentation

**Service:** ML Service
**Version:** 0.1.0
**Port:** 8002
**Architecture:** Schema-per-tenant (v5.0)
**Date:** November 2025

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Database Architecture](#2-database-architecture)
3. [Request Flow](#3-request-flow)
4. [Multi-Tenant Isolation](#4-multi-tenant-isolation)
5. [ML Service Components](#5-ml-service-components)
6. [Integration Points](#6-integration-points)
7. [Scalability Considerations](#7-scalability-considerations)

---

## 1. System Architecture

### 1.1 Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    IndustryFlow Platform                     │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│ API Gateway  │    │  ML Service  │    │Alert Service │
│  (Port 8000) │    │  (Port 8002) │    │  (Port 8001) │
└──────────────┘    └──────────────┘    └──────────────┘
        │                     │                     │
        └─────────────────────┴─────────────────────┘
                              │
                  ┌───────────┴───────────┐
                  │                       │
                  ▼                       ▼
        ┌──────────────────┐    ┌──────────────────┐
        │  TimescaleDB     │    │    MLflow        │
        │  (industryflow)  │    │    (mlflow DB)   │
        │                  │    │                  │
        │  - ml_models     │    │  - experiments   │
        │  - equipment     │    │  - runs          │
        │  - sensors       │    │  - metrics       │
        │  - alerts        │    │  - params        │
        └──────────────────┘    └──────────────────┘
                  │                       │
                  └───────────┬───────────┘
                              │
                  ┌───────────┴───────────┐
                  │                       │
                  ▼                       ▼
        ┌──────────────────┐    ┌──────────────────┐
        │      MinIO       │    │   Jupyter Lab    │
        │  (Model Storage) │    │   (Port 8888)    │
        └──────────────────┘    └──────────────────┘
```

### 1.2 ML Service Internal Architecture

```
┌────────────────────────────────────────────────────┐
│             ML Service API (FastAPI)                │
├────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │   Health     │  │    Models    │  │  MLflow  │ │
│  │   Router     │  │    Router    │  │  Router  │ │
│  └──────┬───────┘  └──────┬───────┘  └────┬─────┘ │
│         │                 │                 │       │
│         └─────────────────┴─────────────────┘       │
│                           │                         │
│                  ┌────────┴────────┐                │
│                  │                 │                │
│         ┌────────▼───────┐  ┌─────▼──────────┐     │
│         │ Authentication │  │  Dependency    │     │
│         │    Module      │  │   Injection    │     │
│         └────────┬───────┘  └─────┬──────────┘     │
│                  │                 │                │
│                  └────────┬────────┘                │
│                           │                         │
│                  ┌────────▼────────┐                │
│                  │                 │                │
│         ┌────────▼───────┐  ┌─────▼──────────┐     │
│         │   ML Repository│  │ MLflow Repository│    │
│         │  (industryflow)│  │   (mlflow DB)   │    │
│         └────────┬───────┘  └─────┬──────────┘     │
│                  │                 │                │
├──────────────────┼─────────────────┼────────────────┤
│  Connection Pools│                 │                │
│  ┌───────────────▼──┐  ┌───────────▼──────────┐    │
│  │ IndustryFlow Pool│  │   MLflow Pool        │    │
│  │ (5-20 conns)     │  │   (5-20 conns)       │    │
│  │ Timeout: 60s     │  │   Timeout: 60s       │    │
│  └───────────────┬──┘  └───────────┬──────────┘    │
│                  │                 │                │
└──────────────────┼─────────────────┼────────────────┘
                   │                 │
                   ▼                 ▼
        ┌──────────────────┐  ┌──────────────────┐
        │   TimescaleDB    │  │   TimescaleDB    │
        │   (industryflow) │  │     (mlflow)     │
        └──────────────────┘  └──────────────────┘
```

### 1.3 Service Configuration

**ML Service API Container:**
- Base Image: python:3.11-slim
- Workers: 4 (uvicorn)
- Port: 8002
- Dependencies: FastAPI, asyncpg, python-jose, MLflow client

**Jupyter Container:**
- Base Image: python:3.11-slim
- Port: 8888
- Purpose: Interactive ML development, model training
- Access: notebooks directory only
- Dependencies: Full ML stack (scikit-learn, pandas, MLflow, optuna)

---

## 2. Database Architecture

### 2.1 Schema-Per-Tenant Pattern

```
┌──────────────────────────────────────────────────┐
│         TimescaleDB (industryflow database)       │
├──────────────────────────────────────────────────┤
│  public schema:                                   │
│  ├── companies (tenant registry)                 │
│  └── user (authentication)                       │
│                                                   │
│  tenant_550e8400_e29b_41d4_a716_446655440000:   │
│  ├── equipment                                   │
│  ├── sensors                                     │
│  ├── sensor_measurements (hypertable)           │
│  ├── alert_rules                                 │
│  ├── alerts (hypertable)                         │
│  └── ml_models                                   │
│                                                   │
│  tenant_550e8400_e29b_41d4_a716_446655440001:   │
│  └── [same structure]                            │
│                                                   │
│  tenant_550e8400_e29b_41d4_a716_446655440002:   │
│  └── [same structure]                            │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│         TimescaleDB (mlflow database)             │
├──────────────────────────────────────────────────┤
│  public schema:                                   │
│  └── (shared MLflow tables)                      │
│                                                   │
│  tenant_550e8400_e29b_41d4_a716_446655440000:   │
│  ├── experiments                                 │
│  ├── runs                                        │
│  ├── metrics                                     │
│  ├── params                                      │
│  ├── tags                                        │
│  ├── registered_models                           │
│  └── model_versions                              │
│                                                   │
│  tenant_550e8400_e29b_41d4_a716_446655440001:   │
│  └── [same structure]                            │
│                                                   │
│  tenant_550e8400_e29b_41d4_a716_446655440002:   │
│  └── [same structure]                            │
└──────────────────────────────────────────────────┘
```

### 2.2 ml_models Table Schema

**Table:** `tenant_{uuid}.ml_models`

| Column Name           | Data Type                  | Constraints | Description |
|-----------------------|----------------------------|-------------|-------------|
| model_id              | uuid                       | PRIMARY KEY | Unique model identifier |
| equipment_id          | uuid                       | FOREIGN KEY | Links to equipment table |
| model_name            | text                       | NOT NULL    | Human-readable name |
| description           | text                       | NULL        | Optional description |
| model_type            | text                       | NOT NULL    | Algorithm type |
| model_version         | integer                    | NOT NULL    | Version number |
| mlflow_run_id         | text                       | NULL        | MLflow tracking |
| mlflow_experiment_id  | text                       | NULL        | MLflow tracking |
| model_path            | text                       | NULL        | S3/MinIO location |
| training_metrics      | jsonb                      | NULL        | Training metrics |
| hyperparameters       | jsonb                      | NULL        | Model hyperparameters |
| feature_names         | text[]                     | NULL        | Array of feature names |
| sensor_ids            | uuid[]                     | NULL        | Array of sensor UUIDs |
| accuracy              | double precision           | NULL        | Model accuracy (0-1) |
| precision_score       | double precision           | NULL        | Precision metric (0-1) |
| recall                | double precision           | NULL        | Recall metric (0-1) |
| f1_score              | double precision           | NULL        | F1 score (0-1) |
| auc_roc               | double precision           | NULL        | AUC-ROC score (0-1) |
| training_samples      | integer                    | NULL        | Number of training samples |
| training_start_date   | timestamp with time zone   | NULL        | Training start |
| training_end_date     | timestamp with time zone   | NULL        | Training end |
| status                | text                       | NOT NULL    | production/staging/archived/active |
| deployed_at           | timestamp with time zone   | NULL        | Deployment timestamp |
| deprecated_at         | timestamp with time zone   | NULL        | Deprecation timestamp |
| created_at            | timestamp with time zone   | DEFAULT NOW() | Creation timestamp |
| updated_at            | timestamp with time zone   | DEFAULT NOW() | Update timestamp |
| created_by            | text                       | NULL        | User who created |

**Indexes:**
- Primary key index on model_id
- Foreign key index on equipment_id
- Index on status for filtering
- Index on created_at for sorting
- Index on model_type for latest model queries

### 2.3 MLflow Database Schema

**Tenant Schema Tables:**

**experiments:**
- experiment_id (integer, PRIMARY KEY)
- name (text)
- artifact_location (text)
- lifecycle_stage (text)
- creation_time (bigint)
- last_update_time (bigint)

**runs:**
- run_uuid (text, PRIMARY KEY)
- experiment_id (integer, FOREIGN KEY)
- user_id (text)
- status (text)
- start_time (bigint)
- end_time (bigint)
- artifact_uri (text)
- lifecycle_stage (text)

**metrics:**
- key (text)
- value (double precision)
- timestamp (bigint)
- run_uuid (text, FOREIGN KEY)
- step (bigint)
- is_nan (boolean)

**params:**
- key (text)
- value (text)
- run_uuid (text, FOREIGN KEY)

**tags:**
- key (text)
- value (text)
- run_uuid (text, FOREIGN KEY)

**registered_models:**
- name (text, PRIMARY KEY)
- creation_timestamp (bigint)
- last_updated_timestamp (bigint)
- description (text)

**model_versions:**
- name (text)
- version (integer)
- creation_timestamp (bigint)
- last_updated_timestamp (bigint)
- description (text)
- user_id (text)
- current_stage (text)
- source (text)
- run_id (text)
- status (text)
- status_message (text)

### 2.4 Database User Permissions

**ml_service_user (industryflow database):**
- Connection Limit: 10
- Statement Timeout: 60s
- Permissions per tenant schema:
  - USAGE on schema
  - SELECT, INSERT, UPDATE, DELETE on all tables
  - SELECT on public.user (for company_id lookup)

**mlflow_user (mlflow database):**
- Connection Limit: 40
- Statement Timeout: 30s
- Permissions per tenant schema:
  - USAGE on schema
  - SELECT, INSERT, UPDATE, DELETE on all tables

---

## 3. Request Flow

### 3.1 API Request Lifecycle

```
1. Client Request
   │
   ├─→ HTTP Request with JWT Bearer Token
   │   URL: POST /api/models/{model_id}/deploy
   │   Headers: Authorization: Bearer <token>
   │   Body: {"model_id": "uuid", "environment": "production"}
   │
2. FastAPI Application
   │
   ├─→ CORS Middleware (validate origin)
   │
   ├─→ Route to models_router
   │
3. Authentication Layer
   │
   ├─→ Dependency: verify_jwt_token()
   │   ├─→ Extract token from header
   │   ├─→ Decode JWT (HS256 algorithm)
   │   ├─→ Extract user_id from 'sub' claim
   │   └─→ Return {"user_id": "uuid", "payload": {...}}
   │
   ├─→ Dependency: get_company_id_dependency()
   │   ├─→ Receive user_id from JWT
   │   ├─→ Get db_pool from app.state
   │   ├─→ Query all tenant schemas:
   │   │   SELECT schema_name FROM information_schema.schemata
   │   │   WHERE schema_name LIKE 'tenant_%'
   │   ├─→ For each tenant schema:
   │   │   ├─→ SET search_path TO {schema_name}, public
   │   │   └─→ SELECT company_id FROM "user" WHERE id = user_id
   │   └─→ Return company_id: "550e8400-e29b-41d4-a716-446655440000"
   │
4. Endpoint Handler
   │
   ├─→ Function: deploy_model()
   │   ├─→ Validate request body (environment value)
   │   ├─→ Get repository from app.state.ml_repository
   │   └─→ Call repository.get_model_by_id(company_id, model_id)
   │
5. Repository Layer
   │
   ├─→ MLRepository.get_model_by_id()
   │   ├─→ Normalize company_id to schema:
   │   │   "tenant_550e8400_e29b_41d4_a716_446655440000"
   │   ├─→ Acquire connection from pool
   │   ├─→ SET search_path TO {schema_name}, public
   │   ├─→ Execute query:
   │   │   SELECT * FROM ml_models WHERE model_id = $1
   │   ├─→ If not found, return None
   │   └─→ Inject company_id: {**dict(row), 'company_id': company_id}
   │
   ├─→ If model exists:
   │   ├─→ repository.update_model_status(company_id, model_id, status)
   │   ├─→ SET search_path TO {schema_name}, public
   │   ├─→ UPDATE ml_models SET status = $1 WHERE model_id = $2
   │   └─→ Return success boolean
   │
6. Response Construction
   │
   ├─→ Endpoint returns JSON:
   │   {
   │     "status": "success",
   │     "model_id": "uuid",
   │     "environment": "production",
   │     "updated_at": "2025-11-08T19:00:00.000000"
   │   }
   │
7. Client Receives Response
   └─→ HTTP 200 OK with JSON body
```

### 3.2 Company ID Resolution Algorithm

```
Input: user_id (UUID from JWT)
Output: company_id (UUID)

Algorithm:
1. Connect to industryflow database
2. Query tenant schemas:
   schemas = SELECT schema_name 
             FROM information_schema.schemata 
             WHERE schema_name LIKE 'tenant_%'
             ORDER BY schema_name

3. For each schema in schemas:
   a. SET search_path TO {schema}, public
   b. company_id = SELECT company_id 
                   FROM "user" 
                   WHERE id = user_id
   c. If company_id found:
      RETURN company_id
   d. Continue to next schema

4. If no schema contains user:
   RAISE HTTPException(404, "User not found")

Complexity: O(n) where n = number of tenant schemas
Typical: n = 3, query time = 5-10ms
```

### 3.3 Schema Routing Pattern

```
Input: company_id (UUID), query, parameters
Output: query results with company_id injected

Algorithm:
1. Normalize company_id to schema name:
   schema = f"tenant_{company_id.replace('-', '_')}"
   
2. Acquire database connection from pool
   
3. Set search path:
   EXECUTE "SET search_path TO {schema}, public"
   
4. Execute query (no company_id in WHERE):
   rows = FETCH query WITH parameters
   
5. Transform results:
   FOR each row in rows:
       result = dict(row)
       result['company_id'] = company_id
       results.append(result)
   
6. Return results

Key Points:
- Schema isolation at PostgreSQL level
- No company_id column in tenant tables
- company_id injected in application layer
- Complete data isolation between tenants
```

---

## 4. Multi-Tenant Isolation

### 4.1 Isolation Mechanisms

**Level 1: Schema-Level Isolation**
```
PostgreSQL Feature: Separate schemas per tenant
Mechanism: SET search_path TO tenant_{uuid}, public
Benefit: Complete data isolation at database level
Verification: Cannot cross-query between schemas
```

**Level 2: Connection Pool Separation**
```
ML Service maintains separate pools for:
- industryflow database (ml_models, equipment, sensors)
- mlflow database (experiments, runs, metrics)

Each pool configured independently:
- Connection limits
- Timeout settings  
- Statement timeouts
```

**Level 3: JWT-Based Authentication**
```
Token contains:
- user_id (sub claim)
- Expiration time
- Signature (HS256)

Server verifies:
- Token signature
- Token expiration
- User existence in tenant schema
```

**Level 4: Application-Level Enforcement**
```
Every API request:
1. Verifies JWT
2. Resolves company_id from user_id
3. Routes to correct tenant schema
4. Returns only tenant's data
```

### 4.2 Tenant Schema Creation

```sql
-- Function to create new tenant schema
CREATE OR REPLACE FUNCTION create_tenant_schema(
    p_company_id UUID,
    p_company_name TEXT
) RETURNS void AS $$
DECLARE
    v_schema_name TEXT;
BEGIN
    -- Generate schema name
    v_schema_name := 'tenant_' || REPLACE(p_company_id::TEXT, '-', '_');
    
    -- Create schema
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', v_schema_name);
    
    -- Create ml_models table
    EXECUTE format('
        CREATE TABLE %I.ml_models (
            model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipment_id UUID,
            model_name TEXT NOT NULL,
            description TEXT,
            model_type TEXT NOT NULL,
            model_version INTEGER NOT NULL,
            mlflow_run_id TEXT,
            mlflow_experiment_id TEXT,
            model_path TEXT,
            training_metrics JSONB,
            hyperparameters JSONB,
            feature_names TEXT[],
            sensor_ids UUID[],
            accuracy DOUBLE PRECISION,
            precision_score DOUBLE PRECISION,
            recall DOUBLE PRECISION,
            f1_score DOUBLE PRECISION,
            auc_roc DOUBLE PRECISION,
            training_samples INTEGER,
            training_start_date TIMESTAMPTZ,
            training_end_date TIMESTAMPTZ,
            status TEXT NOT NULL,
            deployed_at TIMESTAMPTZ,
            deprecated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            created_by TEXT,
            FOREIGN KEY (equipment_id) REFERENCES %I.equipment(equipment_id) ON DELETE CASCADE
        )', v_schema_name, v_schema_name);
    
    -- Grant permissions to ml_service_user
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO ml_service_user', v_schema_name);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO ml_service_user', v_schema_name);
END;
$$ LANGUAGE plpgsql;
```

### 4.3 Security Verification

**Test 1: Cross-Tenant Data Access**
```python
# User from Company A tries to access Company B data
user_a_token = authenticate("user@companyA.com")
response = requests.get(
    "http://localhost:8002/api/models",
    headers={"Authorization": f"Bearer {user_a_token}"}
)
# Result: Returns only Company A models
# Company B models: Not visible
```

**Test 2: Schema Isolation**
```sql
-- Connect as ml_service_user
SET search_path TO tenant_companyA, public;
SELECT * FROM ml_models;
-- Result: Only Company A models

SET search_path TO tenant_companyB, public;
SELECT * FROM ml_models;
-- Result: Only Company B models

-- Attempt cross-query
SELECT * FROM tenant_companyB.ml_models;
-- Result: Permission denied (no cross-schema access)
```

---

## 5. ML Service Components

### 5.1 FastAPI Application

**File:** `main.py`

**Responsibilities:**
- Application initialization
- CORS middleware configuration
- Router registration
- Database pool creation
- Repository instantiation
- Startup/shutdown lifecycle management

**Startup Sequence:**
```
1. Load configuration from environment
2. Validate required variables
3. Create industryflow database pool
4. Create mlflow database pool
5. Instantiate MLRepository with industryflow pool
6. Instantiate MLflowRepository with mlflow pool
7. Store repositories in app.state
8. Register routers (health, models, mlflow)
9. Start uvicorn workers (4 workers)
```

**Configuration:**
```python
config = {
    'DB_HOST': 'timescaledb',
    'DB_PORT': 5432,
    'DB_NAME': 'industryflow',
    'ML_SERVICE_DB_USER': 'ml_service_user',
    'ML_SERVICE_DB_PASSWORD': '***',
    'MLFLOW_DB_NAME': 'mlflow',
    'MLFLOW_DB_USER': 'mlflow_user',
    'MLFLOW_DB_PASSWORD': '***',
    'JWT_SECRET_KEY': '***',
    'JWT_ALGORITHM': 'HS256',
    'MLFLOW_TRACKING_URI': 'http://mlflow:5000'
}
```

### 5.2 Authentication Module

**File:** `auth.py`

**Functions:**
1. `verify_jwt_token()` - Validates JWT and extracts user_id
2. `get_company_id_dependency()` - Resolves company_id from user_id

**JWT Validation:**
```python
Algorithm: HS256
Payload Structure:
{
    "sub": "user_id (UUID)",
    "exp": expiration_timestamp,
    "iat": issued_at_timestamp
}

Validation Steps:
1. Extract Bearer token from Authorization header
2. Decode with JWT_SECRET_KEY
3. Verify signature
4. Check expiration
5. Extract 'sub' claim
6. Return user information
```

### 5.3 Repository Layer

**Files:** `repository.py`

**Classes:**

**MLRepository:**
- Manages ml_models table in industryflow database
- Methods:
  - get_all_models(company_id, status, limit)
  - get_model_by_id(company_id, model_id)
  - update_model_status(company_id, model_id, status)
  - delete_model(company_id, model_id)
  - get_latest_model(company_id, model_type)
  - compare_models(company_id, model_ids)

**MLflowRepository:**
- Manages MLflow tables in mlflow database
- Methods:
  - get_all_experiments(company_id)
  - get_experiment_by_id(company_id, experiment_id)
  - get_runs_for_experiment(company_id, experiment_id, max_results)
  - get_run_details(company_id, run_id)
  - get_registered_models(company_id)
  - get_registered_model_with_versions(company_id, model_name)

**Repository Pattern:**
```python
class MLRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def get_model_by_id(self, company_id: str, model_id: str):
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            row = await conn.fetchrow(
                "SELECT * FROM ml_models WHERE model_id = $1",
                model_id
            )
            if not row:
                return None
            return {**dict(row), 'company_id': company_id}
```

### 5.4 Router Layer

**Files:** `routers/*.py`

**health.py:**
- `/` - Service information
- `/health` - Health check with model count

**models.py:**
- `/api/models` - CRUD operations on ml_models
- 7 endpoints for model management

**mlflow.py:**
- `/api/mlflow/experiments` - Experiment tracking
- `/api/mlflow/runs` - Run management
- `/api/mlflow/models` - Registered models
- 9 endpoints for MLflow integration

### 5.5 Jupyter Lab

**Purpose:** Interactive ML development environment

**Capabilities:**
- Model training with scikit-learn, XGBoost
- Hyperparameter optimization with Optuna
- MLflow experiment tracking
- Direct database access for feature engineering
- Visualization with matplotlib, seaborn, plotly

**Access Pattern:**
```
1. Connect to Jupyter: http://localhost:8888
2. Navigate to /notebooks directory
3. Create/open notebook
4. Import libraries (pandas, sklearn, mlflow)
5. Connect to databases using environment variables
6. Train models
7. Log experiments to MLflow
8. Register models in ml_models table
```

---

## 6. Integration Points

### 6.1 API Gateway Integration

**Connection:** ML Service ← API Gateway

**Flow:**
```
1. Client → API Gateway (authentication)
2. API Gateway → ML Service (with JWT)
3. ML Service validates JWT
4. ML Service returns data
5. API Gateway → Client
```

**No Direct Integration:** Currently ML Service operates independently

### 6.2 MLflow Integration

**Connection:** ML Service ↔ MLflow Server

**Tracking URI:** `http://mlflow:5000`

**Integration Pattern:**
```
Model Training (Jupyter):
1. Start MLflow run
2. Log parameters
3. Log metrics
4. Log model artifacts to MinIO
5. Register model in MLflow
6. Record in ml_models table

Model Query (API):
1. Query ml_models table for metadata
2. Query mlflow database for run details
3. Return combined information
```

### 6.3 MinIO Integration

**Connection:** ML Service → MinIO (S3-compatible storage)

**Endpoint:** `minio:9000`

**Usage:**
- MLflow artifact storage
- Model binary storage
- Training dataset storage

**Access Pattern:**
```python
import mlflow
mlflow.set_tracking_uri('http://mlflow:5000')

# MLflow automatically stores to MinIO
mlflow.sklearn.log_model(model, "model")

# Retrieval
model = mlflow.sklearn.load_model(f"runs:/{run_id}/model")
```

### 6.4 TimescaleDB Integration

**Dual Database Connection:**

**industryflow database:**
- Purpose: Application data
- Tables: ml_models, equipment, sensors, alerts
- User: ml_service_user
- Pool: 5-20 connections

**mlflow database:**
- Purpose: Experiment tracking
- Tables: experiments, runs, metrics, params
- User: mlflow_user
- Pool: 5-20 connections

**Connection Management:**
```python
# Separate pools for each database
app.state.db_pool = asyncpg.create_pool(
    database='industryflow', user='ml_service_user', ...
)
app.state.mlflow_pool = asyncpg.create_pool(
    database='mlflow', user='mlflow_user', ...
)

# Use appropriate pool per query
repository = MLRepository(app.state.db_pool)
mlflow_repository = MLflowRepository(app.state.mlflow_pool)
```

---

## 7. Scalability Considerations

### 7.1 Connection Pool Scaling

**Current Configuration:**
```
ML Service:
- IndustryFlow Pool: 5-20 connections
- MLflow Pool: 5-20 connections
- Workers: 4
- Total capacity: 80 connections per database

Database Limits:
- ml_service_user: 10 connections
- mlflow_user: 40 connections
```

**Bottleneck:** ml_service_user connection limit (10)

**Solution for Scale:**
```
Option 1: Increase per-user connection limit
ALTER ROLE ml_service_user CONNECTION LIMIT 50;

Option 2: Add more ML Service instances with load balancer
- Instance 1: 10 connections
- Instance 2: 10 connections
- Instance 3: 10 connections
Total: 30 connections available

Option 3: PgBouncer connection pooling
- Single PgBouncer instance
- ML Service → PgBouncer → TimescaleDB
- Transaction pooling mode
```

### 7.2 Horizontal Scaling

**Current:** Single ML Service instance with 4 workers

**Scale Pattern:**
```
Load Balancer (nginx/HAProxy)
        ├─→ ML Service Instance 1 (workers: 4)
        ├─→ ML Service Instance 2 (workers: 4)
        └─→ ML Service Instance 3 (workers: 4)
                    │
            TimescaleDB (connection pooling)
```

**Considerations:**
- Stateless design enables horizontal scaling
- Connection pool per instance
- JWT validation per instance (no session sharing)
- Database connection limit must scale with instances

### 7.3 Database Performance

**Query Optimization:**
```
1. Indexes on ml_models:
   - model_id (PRIMARY KEY)
   - equipment_id (FOREIGN KEY)
   - status (frequent filter)
   - created_at (sorting)
   - model_type (latest model queries)

2. Schema routing overhead:
   - SET search_path: ~1ms
   - Query execution: 5-50ms depending on size
   - Total: 6-51ms per request

3. Connection pooling:
   - Pool acquisition: <1ms
   - Reuse reduces overhead
```

**Optimization Strategies:**
```
1. Read replicas for query-heavy workloads
2. Materialized views for complex aggregations
3. Partitioning for large ml_models tables
4. Query result caching in Redis
```

### 7.4 Tenant Schema Growth

**Current:** 3 tenant schemas

**At Scale (1000 tenants):**
```
Company ID Resolution:
- Query 1000 schemas sequentially: 50-100ms
- Solution: Cache user→company mapping in Redis
- Cached lookup: <1ms

Schema Creation:
- Automated via create_tenant_schema() function
- Happens once per tenant
- Time: ~500ms per tenant
```

**Optimization:**
```python
# Cache user→company_id mapping
redis_key = f"user_company:{user_id}"
company_id = await redis.get(redis_key)

if not company_id:
    # Query database
    company_id = resolve_from_database(user_id)
    # Cache for 1 hour
    await redis.setex(redis_key, 3600, company_id)
```

### 7.5 MLflow Scalability

**Current:** Single MLflow server

**At Scale:**
```
Option 1: MLflow with PostgreSQL backend (current)
- Scales to ~10K experiments
- Bottleneck: Database query performance

Option 2: MLflow with dedicated tracking server pool
- Multiple MLflow instances
- Shared PostgreSQL backend
- Load balanced

Option 3: S3-backed artifact storage (current via MinIO)
- Unlimited artifact storage
- Parallel reads/writes
```

**Performance Targets:**
```
- Model query: <100ms
- Experiment listing: <200ms
- Run details with metrics: <500ms
- Artifact download: <5s (depends on size)
```

---

## End of Architecture Documentation

**Key Architectural Principles:**
1. Schema-per-tenant provides complete data isolation
2. Dual database pools separate application and tracking data
3. JWT-based authentication with company_id resolution
4. Stateless design enables horizontal scaling
5. Connection pooling optimizes database access
6. Repository pattern abstracts database logic
7. FastAPI async architecture for high concurrency
