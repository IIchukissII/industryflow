<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Feature Engineering Service Architecture Documentation

**Service:** Feature Engineering Registry (ML Service Extension)
**Version:** 0.1.0
**Port:** 8002 (shared with ML Service)
**Architecture:** Schema-per-tenant (v5.0)
**Date:** November 2025

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Database Architecture](#2-database-architecture)
3. [Request Flow](#3-request-flow)
4. [Multi-Tenant Isolation](#4-multi-tenant-isolation)
5. [Feature Engineering Components](#5-feature-engineering-components)
6. [Transformation Types](#6-transformation-types)
7. [Integration with ML Pipeline](#7-integration-with-ml-pipeline)
8. [Scalability Considerations](#8-scalability-considerations)

---

## 1. System Architecture

### 1.1 Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Jupyter Notebook (Training)                  │
│  ┌────────────┐     ┌──────────────┐     ┌──────────────┐      │
│  │ Raw Sensor │────>│ Feature Eng  │────>│   XGBoost    │      │
│  │    Data    │     │ Transforms   │     │   Training   │      │
│  └────────────┘     └──────────────┘     └──────┬───────┘      │
│                                                   │              │
│                     Register Feature Config       │              │
│                            +                      │              │
│                     Register Model (MLflow)       │              │
└────────────────────────────────────────────────┬──┴─────────────┘
                                                 │
                                                 ▼
                            ┌───────────────────────────────┐
                            │     ML Service Database       │
                            ├───────────────────────────────┤
                            │ • feature_engineering_configs │
                            │   - transformations (JSONB)   │
                            │   - versioning                │
                            │                               │
                            │ • ml_models                   │
                            │   - feature_config_id (FK)    │
                            │   - mlflow_run_id             │
                            │   - training_metrics          │
                            └──────────┬────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────┐
│               Production Inference (Real-Time)                   │
│                                                                  │
│  ┌────────────┐     ┌──────────────┐     ┌──────────────┐     │
│  │ Streaming  │────>│   Retrieve   │────>│   Retrieve   │     │
│  │   Sensor   │     │    Model     │     │ Feature Cfg  │     │
│  │    Data    │     │   Metadata   │     │              │     │
│  └────────────┘     └──────────────┘     └──────┬───────┘     │
│                                                   │              │
│                                                   ▼              │
│                                          ┌──────────────┐       │
│                                          │   Apply 65   │       │
│                                          │Transformations│       │
│                                          └──────┬───────┘       │
│                                                 │               │
│                                                 ▼               │
│                                          ┌──────────────┐       │
│                                          │   XGBoost    │       │
│                                          │  Inference   │       │
│                                          │  (MLflow)    │       │
│                                          └──────┬───────┘       │
│                                                 │               │
│                                                 ▼               │
│                                          ┌──────────────┐       │
│                                          │   Anomaly    │       │
│                                          │  Prediction  │       │
│                                          └──────────────┘       │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Feature Engineering Service Architecture

```
┌────────────────────────────────────────────────────┐
│       ML Service API (FastAPI) - Port 8002          │
├────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │    Models    │  │   Feature    │  │  MLflow  │ │
│  │    Router    │  │   Configs    │  │  Router  │ │
│  │              │  │   Router     │  │          │ │
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
│         │   ML Repository│  │ Feature Config │     │
│         │  (models table)│  │   Repository   │     │
│         └────────┬───────┘  └─────┬──────────┘     │
│                  │                 │                │
├──────────────────┼─────────────────┼────────────────┤
│  Connection Pool │                 │                │
│  ┌───────────────▼─────────────────▼──────────┐    │
│  │     IndustryFlow Database Pool             │    │
│  │     (5-20 connections)                     │    │
│  │     Timeout: 60s                           │    │
│  └───────────────┬────────────────────────────┘    │
│                  │                                  │
└──────────────────┼──────────────────────────────────┘
                   │
                   ▼
        ┌──────────────────┐
        │   TimescaleDB    │
        │   (industryflow) │
        │                  │
        │  tenant_* schemas│
        │  - ml_models     │
        │  - feature_eng_  │
        │    configs       │
        └──────────────────┘
```

### 1.3 Design Principles

**Reproducibility:**
- Feature transformations defined once in database
- Identical transformations from training to production
- Version control for transformation evolution

**Flexibility:**
- Declarative JSON-based configuration
- Extensible transformation type system
- Easy to add new transformation types

**Integration:**
- Foreign key linkage between models and configs
- Single source of truth for transformations
- Multi-tenant isolation at schema level

---

## 2. Database Architecture

### 2.1 feature_engineering_configs Table Schema

**Table:** `tenant_{uuid}.feature_engineering_configs`

| Column Name           | Data Type                  | Constraints | Description |
|-----------------------|----------------------------|-------------|-------------|
| id                    | uuid                       | PRIMARY KEY | Unique config identifier |
| name                  | text                       | NOT NULL    | Human-readable name |
| description           | text                       | NULL        | Optional description |
| equipment_type        | text                       | NOT NULL    | Equipment classification |
| base_sensors          | text[]                     | NOT NULL    | Array of sensor names |
| transformations       | jsonb                      | NOT NULL    | Transformation definitions |
| version               | text                       | NOT NULL    | Semantic version (e.g., "1.0.0") |
| status                | text                       | NOT NULL    | active/deprecated/draft |
| created_at            | timestamp with time zone   | DEFAULT NOW() | Creation timestamp |
| updated_at            | timestamp with time zone   | DEFAULT NOW() | Update timestamp |
| created_by            | text                       | NULL        | User who created |

**Indexes:**
- Primary key index on id
- Index on equipment_type for filtering
- Index on status for active config queries
- Index on created_at for versioning

**Check Constraints:**
```sql
ALTER TABLE feature_engineering_configs
ADD CONSTRAINT feature_config_status_check
CHECK (status IN ('active', 'deprecated', 'draft'));
```

### 2.2 ml_models Table Integration

**Enhanced ml_models table includes:**

| Column Name           | Data Type                  | Constraints | Description |
|-----------------------|----------------------------|-------------|-------------|
| feature_config_id     | uuid                       | FOREIGN KEY | Links to feature_engineering_configs |
| equipment_type        | text                       | NULL        | Equipment classification |

**Foreign Key Constraint:**
```sql
ALTER TABLE ml_models
ADD CONSTRAINT fk_feature_config
FOREIGN KEY (feature_config_id)
REFERENCES feature_engineering_configs(id)
ON DELETE SET NULL;
```

**Benefits:**
- Model knows which transformations were used during training
- Production inference uses identical transformations
- Configuration versioning enables reproducibility

### 2.3 Transformations JSONB Structure

**Schema:**
```json
{
  "transformations": [
    {
      "name": "feature_name",
      "type": "identity|polynomial|interaction|statistical",
      "sensor": "sensor_name",           // for identity, polynomial, statistical
      "sensors": ["sensor1", "sensor2"], // for interaction
      "params": {                        // type-specific parameters
        "operation": "ratio|difference|product",  // interaction
        "power": 2,                               // polynomial
        "stat_type": "deviation_from_run_mean"    // statistical
      }
    }
  ]
}
```

**Example Configuration:**
```json
{
  "name": "TEP Binary Anomaly Detection - Balanced 50/50",
  "equipment_type": "tep_reactor",
  "base_sensors": [
    "xmeas_1", "xmeas_2", "xmeas_3", ..., "xmv_11"
  ],
  "transformations": [
    {
      "name": "xmeas_1",
      "type": "identity",
      "sensor": "xmeas_1"
    },
    {
      "name": "xmeas_7_xmeas_8_ratio",
      "type": "interaction",
      "sensors": ["xmeas_7", "xmeas_8"],
      "params": {"operation": "ratio"}
    },
    {
      "name": "xmeas_7_squared",
      "type": "polynomial",
      "sensor": "xmeas_7",
      "params": {"power": 2}
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

### 2.4 Schema Creation

**Migration:** `002_feature_engineering_registry.sql`

```sql
-- Create feature_engineering_configs table in tenant schema
CREATE TABLE IF NOT EXISTS {schema}.feature_engineering_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    equipment_type TEXT NOT NULL,
    base_sensors TEXT[] NOT NULL,
    transformations JSONB NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by TEXT,
    CONSTRAINT feature_config_status_check
        CHECK (status IN ('active', 'deprecated', 'draft'))
);

-- Create indexes
CREATE INDEX idx_feature_configs_equipment_type
    ON {schema}.feature_engineering_configs(equipment_type);
CREATE INDEX idx_feature_configs_status
    ON {schema}.feature_engineering_configs(status);
CREATE INDEX idx_feature_configs_created_at
    ON {schema}.feature_engineering_configs(created_at DESC);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE
    ON {schema}.feature_engineering_configs
    TO ml_service_user;
```

**Migration:** `003_add_feature_config_to_models.sql`

```sql
-- Add feature_config_id to ml_models table
ALTER TABLE {schema}.ml_models
ADD COLUMN feature_config_id UUID,
ADD COLUMN equipment_type TEXT;

-- Add foreign key constraint
ALTER TABLE {schema}.ml_models
ADD CONSTRAINT fk_feature_config
FOREIGN KEY (feature_config_id)
REFERENCES {schema}.feature_engineering_configs(id)
ON DELETE SET NULL;

-- Create index for foreign key lookups
CREATE INDEX idx_ml_models_feature_config_id
    ON {schema}.ml_models(feature_config_id);
```

---

## 3. Request Flow

### 3.1 Feature Config Registration Flow

```
1. Client Request (Jupyter Notebook or API)
   │
   ├─→ POST /api/feature-configs
   │   Headers: Authorization: Bearer <token>
   │   Body: {
   │     "name": "TEP Anomaly Detection Config",
   │     "equipment_type": "tep_reactor",
   │     "base_sensors": ["xmeas_1", ...],
   │     "transformations": [...],
   │     "version": "1.0.0",
   │     "status": "active"
   │   }
   │
2. FastAPI Application
   │
   ├─→ CORS Middleware
   ├─→ Route to feature_configs_router
   │
3. Authentication Layer
   │
   ├─→ Dependency: verify_jwt_token()
   │   ├─→ Extract user_id from JWT 'sub' claim
   │   └─→ Return {"user_id": "uuid"}
   │
   ├─→ Dependency: get_company_id_dependency()
   │   ├─→ Query tenant schemas for user
   │   └─→ Return company_id
   │
4. Endpoint Handler
   │
   ├─→ Function: create_feature_config()
   │   ├─→ Validate transformations schema
   │   ├─→ Check equipment_type validity
   │   └─→ Call repository.create_feature_config()
   │
5. Repository Layer
   │
   ├─→ FeatureConfigRepository.create_feature_config()
   │   ├─→ Normalize company_id to schema:
   │   │   "tenant_550e8400_e29b_41d4_a716_446655440000"
   │   ├─→ Acquire connection from pool
   │   ├─→ SET search_path TO {schema_name}, public
   │   ├─→ Serialize transformations to JSON:
   │   │   transformations_json = json.dumps(transformations)
   │   ├─→ Execute INSERT:
   │   │   INSERT INTO feature_engineering_configs
   │   │   (name, equipment_type, base_sensors, transformations, ...)
   │   │   VALUES ($1, $2, $3, $4, ...)
   │   │   RETURNING *
   │   └─→ Return created config with company_id injected
   │
6. Response Construction
   │
   ├─→ Endpoint returns JSON (201 Created):
   │   {
   │     "id": "3d456f9b-acfc-4e16-9f3b-dcd65da9430f",
   │     "name": "TEP Anomaly Detection Config",
   │     "equipment_type": "tep_reactor",
   │     "base_sensors": [...],
   │     "transformations": [...],
   │     "version": "1.0.0",
   │     "status": "active",
   │     "created_at": "2025-11-22T10:00:00.000000"
   │   }
   │
7. Client Receives Response
   └─→ HTTP 201 Created with feature config ID
```

### 3.2 Feature Config Retrieval Flow

```
1. Client Request (Production Inference)
   │
   ├─→ GET /api/feature-configs/{config_id}
   │   Headers: Authorization: Bearer <token>
   │
2. Authentication & Routing
   │
   ├─→ Verify JWT → Extract user_id
   ├─→ Resolve company_id from user_id
   │
3. Repository Query
   │
   ├─→ FeatureConfigRepository.get_config_by_id()
   │   ├─→ SET search_path TO tenant_{company_id}
   │   ├─→ SELECT * FROM feature_engineering_configs
   │   │   WHERE id = $1
   │   ├─→ Parse JSONB transformations
   │   └─→ Return config dict
   │
4. Response
   │
   └─→ HTTP 200 OK with feature config
       Including all transformation definitions
```

### 3.3 Real-Time Inference Flow

```
1. Streaming Sensor Data Arrives
   │   {"xmeas_1": 2705.5, "xmeas_2": 3780.2, ...}
   │
2. Retrieve Model Metadata
   │
   ├─→ GET /api/models/{model_id}
   │   Returns: {
   │     "model_id": "...",
   │     "feature_config_id": "3d456f9b...",
   │     "mlflow_run_id": "af71ddfc...",
   │     "feature_names": ["xmeas_1", "xmeas_7_xmeas_8_ratio", ...],
   │     ...
   │   }
   │
3. Retrieve Feature Engineering Config
   │
   ├─→ GET /api/feature-configs/{feature_config_id}
   │   Returns: {
   │     "base_sensors": ["xmeas_1", ..., "xmv_11"],
   │     "transformations": [
   │       {"name": "xmeas_1", "type": "identity", ...},
   │       {"name": "xmeas_7_xmeas_8_ratio", "type": "interaction", ...},
   │       ...
   │     ]
   │   }
   │
4. Apply Feature Engineering
   │
   ├─→ For each transformation in config:
   │   │
   │   ├─→ If type == "identity":
   │   │   └─→ feature_value = sensor_data[sensor]
   │   │
   │   ├─→ If type == "polynomial":
   │   │   └─→ feature_value = sensor_data[sensor] ** power
   │   │
   │   ├─→ If type == "interaction":
   │   │   ├─→ If operation == "ratio":
   │   │   │   └─→ feature_value = sensor_data[s1] / sensor_data[s2]
   │   │   ├─→ If operation == "difference":
   │   │   │   └─→ feature_value = sensor_data[s1] - sensor_data[s2]
   │   │   └─→ If operation == "product":
   │   │       └─→ feature_value = sensor_data[s1] * sensor_data[s2]
   │   │
   │   └─→ If type == "statistical":
   │       └─→ feature_value = sensor_data[sensor] - baseline_mean
   │
5. Prepare Feature Vector
   │
   ├─→ Order features according to model.feature_names:
   │   feature_vector = [
   │     engineered_features[fname]
   │     for fname in model.feature_names
   │   ]
   │   Result: [2705.5, 35.1, 7023600.25, 5.5, ...]
   │
6. Load Model from MLflow
   │
   ├─→ mlflow.sklearn.load_model(f"runs:/{mlflow_run_id}/model")
   │   Returns: XGBoost classifier
   │
7. Run Inference
   │
   ├─→ predictions = model.predict(feature_vector)
   ├─→ probabilities = model.predict_proba(feature_vector)
   │   Returns: [0] (normal) or [1] (anomaly)
   │            [[0.15, 0.85]] (85% probability of anomaly)
   │
8. Store or Return Prediction
   │
   └─→ Return: {
       "prediction": 1,
       "anomaly_probability": 0.85,
       "timestamp": "2025-11-22T12:00:00.000000"
     }
```

---

## 4. Multi-Tenant Isolation

### 4.1 Isolation Mechanisms

**Level 1: Schema-Level Isolation**
```
PostgreSQL Feature: Separate schemas per tenant
Mechanism: SET search_path TO tenant_{uuid}, public
Benefit: Complete data isolation at database level

Each tenant has isolated:
- feature_engineering_configs table
- ml_models table (with foreign key to configs)
- All transformation definitions
```

**Level 2: Foreign Key Constraints**
```
Models ←[FK]→ Feature Configs

Benefits:
- Referential integrity
- Cannot reference configs from other tenants
- Cascade behavior on config deletion (SET NULL)
```

**Level 3: Application-Level Enforcement**
```
Every API request:
1. Verifies JWT
2. Resolves company_id from user_id
3. Routes to correct tenant schema
4. Returns only tenant's feature configs
```

### 4.2 Cross-Tenant Prevention

**Test Scenario:**
```python
# User from Company A tries to access Company B config
user_a_token = authenticate("user@companyA.com")
config_b_id = "company_b_config_uuid"

response = requests.get(
    f"http://localhost:8002/api/feature-configs/{config_b_id}",
    headers={"Authorization": f"Bearer {user_a_token}"}
)

# Result: 404 Not Found
# Company B config is in different schema, invisible to Company A
```

**Database-Level Verification:**
```sql
-- Connect as ml_service_user
SET search_path TO tenant_companyA, public;
SELECT * FROM feature_engineering_configs;
-- Result: Only Company A configs

SET search_path TO tenant_companyB, public;
SELECT * FROM feature_engineering_configs;
-- Result: Only Company B configs

-- Attempt cross-schema access
SELECT * FROM tenant_companyB.feature_engineering_configs;
-- Result: Permission denied
```

---

## 5. Feature Engineering Components

### 5.1 Repository Layer

**File:** `api/repository.py`

**FeatureConfigRepository Methods:**

```python
class FeatureConfigRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_feature_config(
        self, company_id: str, config_data: dict
    ) -> dict:
        """Create new feature engineering configuration"""
        schema_name = normalize_company_id_to_schema(company_id)

        # Serialize transformations to JSON
        transformations_json = json.dumps(config_data['transformations'])

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            row = await conn.fetchrow(
                """
                INSERT INTO feature_engineering_configs
                (name, description, equipment_type, base_sensors,
                 transformations, version, status, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING *
                """,
                config_data['name'],
                config_data.get('description'),
                config_data['equipment_type'],
                config_data['base_sensors'],
                transformations_json,
                config_data['version'],
                config_data['status'],
                config_data.get('created_by')
            )

            # Parse transformations back to dict
            result = dict(row)
            result['transformations'] = json.loads(result['transformations'])
            result['company_id'] = company_id
            return result

    async def get_config_by_id(
        self, company_id: str, config_id: str
    ) -> Optional[dict]:
        """Retrieve feature config by ID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            row = await conn.fetchrow(
                """
                SELECT * FROM feature_engineering_configs
                WHERE id = $1
                """,
                config_id
            )

            if not row:
                return None

            result = dict(row)
            result['transformations'] = json.loads(result['transformations'])
            result['company_id'] = company_id
            return result

    async def get_all_configs(
        self, company_id: str, equipment_type: Optional[str] = None,
        status: str = 'active'
    ) -> List[dict]:
        """List all feature configs with optional filtering"""
        schema_name = normalize_company_id_to_schema(company_id)

        query = "SELECT * FROM feature_engineering_configs WHERE status = $1"
        params = [status]

        if equipment_type:
            query += " AND equipment_type = $2"
            params.append(equipment_type)

        query += " ORDER BY created_at DESC"

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            rows = await conn.fetch(query, *params)

            results = []
            for row in rows:
                result = dict(row)
                result['transformations'] = json.loads(result['transformations'])
                result['company_id'] = company_id
                results.append(result)

            return results

    async def update_config_status(
        self, company_id: str, config_id: str, status: str
    ) -> bool:
        """Update configuration status (active/deprecated/draft)"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            result = await conn.execute(
                """
                UPDATE feature_engineering_configs
                SET status = $1, updated_at = NOW()
                WHERE id = $2
                """,
                status, config_id
            )

            return result == "UPDATE 1"
```

### 5.2 Router Layer

**File:** `api/routers/feature_configs.py`

**Endpoints:**

```python
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter(prefix="/api/feature-configs", tags=["feature-configs"])

class FeatureConfigCreate(BaseModel):
    name: str
    description: Optional[str] = None
    equipment_type: str
    base_sensors: List[str]
    transformations: List[dict]
    version: str
    status: str = "active"

class FeatureConfigResponse(BaseModel):
    id: str
    company_id: str
    name: str
    description: Optional[str]
    equipment_type: str
    base_sensors: List[str]
    transformations: List[dict]
    version: str
    status: str
    created_at: str
    updated_at: str

@router.post("", status_code=201, response_model=FeatureConfigResponse)
async def create_feature_config(
    config: FeatureConfigCreate,
    user_data: dict = Depends(verify_jwt_token),
    company_id: str = Depends(get_company_id_dependency),
    repository: FeatureConfigRepository = Depends(get_repository)
):
    """
    Create a new feature engineering configuration

    This endpoint registers a new set of feature transformations
    that can be referenced by ML models for reproducible feature engineering.
    """
    config_data = config.dict()
    config_data['created_by'] = user_data['user_id']

    result = await repository.create_feature_config(company_id, config_data)
    return result

@router.get("/{config_id}", response_model=FeatureConfigResponse)
async def get_feature_config(
    config_id: str,
    company_id: str = Depends(get_company_id_dependency),
    repository: FeatureConfigRepository = Depends(get_repository)
):
    """
    Retrieve a specific feature engineering configuration

    Used during production inference to retrieve transformation definitions.
    """
    config = await repository.get_config_by_id(company_id, config_id)

    if not config:
        raise HTTPException(status_code=404, detail="Feature config not found")

    return config

@router.get("", response_model=List[FeatureConfigResponse])
async def list_feature_configs(
    equipment_type: Optional[str] = None,
    status: str = "active",
    company_id: str = Depends(get_company_id_dependency),
    repository: FeatureConfigRepository = Depends(get_repository)
):
    """
    List all feature engineering configurations

    Supports filtering by equipment_type and status.
    """
    configs = await repository.get_all_configs(
        company_id, equipment_type, status
    )
    return configs
```

### 5.3 Transformation Engine

**File:** `api/feature_engineering/transformations.py`

```python
from typing import Dict, Any, List

def apply_transformation(
    sensor_data: Dict[str, float],
    transform: Dict[str, Any],
    baseline_means: Dict[str, float] = None
) -> float:
    """
    Apply a single transformation to raw sensor data

    Args:
        sensor_data: Dictionary of sensor names to values
        transform: Transformation definition from config
        baseline_means: Optional baseline values for statistical transforms

    Returns:
        Engineered feature value
    """
    trans_type = transform['type']
    params = transform.get('params', {})

    if trans_type == 'identity':
        # Direct sensor value pass-through
        sensor = transform['sensor']
        return sensor_data.get(sensor, 0.0)

    elif trans_type == 'polynomial':
        # Power transformation (x^n)
        sensor = transform['sensor']
        power = params.get('power', 2)
        return sensor_data.get(sensor, 0.0) ** power

    elif trans_type == 'interaction':
        # Multi-sensor interaction
        sensors = transform['sensors']
        operation = params.get('operation', 'product')

        if operation == 'ratio':
            numerator = sensor_data.get(sensors[0], 0.0)
            denominator = sensor_data.get(sensors[1], 1.0)
            return numerator / (denominator + 1e-10)  # Avoid division by zero

        elif operation == 'difference':
            return sensor_data.get(sensors[0], 0.0) - sensor_data.get(sensors[1], 0.0)

        elif operation == 'product':
            return sensor_data.get(sensors[0], 0.0) * sensor_data.get(sensors[1], 0.0)

    elif trans_type == 'statistical':
        # Statistical transformation (deviation from baseline)
        sensor = transform['sensor']
        stat_type = params.get('stat_type', 'deviation_from_run_mean')

        if stat_type == 'deviation_from_run_mean':
            if not baseline_means:
                baseline_means = {}
            baseline = baseline_means.get(sensor, sensor_data.get(sensor, 0.0))
            return sensor_data.get(sensor, 0.0) - baseline

    return 0.0

def engineer_features(
    sensor_data: Dict[str, float],
    feature_config: Dict[str, Any],
    feature_names: List[str],
    baseline_means: Dict[str, float] = None
) -> List[float]:
    """
    Apply all transformations from config and return ordered feature vector

    Args:
        sensor_data: Raw sensor readings
        feature_config: Feature engineering configuration from database
        feature_names: Ordered list of feature names (from model)
        baseline_means: Optional baseline values for statistical transforms

    Returns:
        Ordered feature vector ready for model inference
    """
    # Apply all transformations
    engineered = {}
    for transform in feature_config['transformations']:
        feature_name = transform['name']
        feature_value = apply_transformation(sensor_data, transform, baseline_means)
        engineered[feature_name] = feature_value

    # Return features in model's expected order
    return [engineered.get(fname, 0.0) for fname in feature_names]
```

---

## 6. Transformation Types

### 6.1 Identity Transformation

**Purpose:** Pass raw sensor value through unchanged

**Definition:**
```json
{
  "name": "xmeas_1",
  "type": "identity",
  "sensor": "xmeas_1"
}
```

**Implementation:**
```python
def apply_identity(sensor_data, transform):
    sensor = transform['sensor']
    return sensor_data.get(sensor, 0.0)
```

**Use Cases:**
- Baseline features
- Sensors with good predictive power as-is
- Minimal preprocessing required

### 6.2 Polynomial Transformation

**Purpose:** Capture non-linear relationships via power functions

**Definition:**
```json
{
  "name": "xmeas_7_squared",
  "type": "polynomial",
  "sensor": "xmeas_7",
  "params": {"power": 2}
}
```

**Implementation:**
```python
def apply_polynomial(sensor_data, transform):
    sensor = transform['sensor']
    power = transform['params'].get('power', 2)
    return sensor_data.get(sensor, 0.0) ** power
```

**Use Cases:**
- Pressure² for energy calculations
- Temperature³ for heat transfer equations
- Flow² for turbulence indicators

**Example:**
```
Input:  xmeas_7 = 2630 (reactor pressure)
Output: xmeas_7_squared = 6,916,900
        xmeas_7_cubed = 18,191,447,000
```

### 6.3 Interaction Transformation

**Purpose:** Capture relationships between multiple sensors

**Subtypes:**

**Ratio Operation:**
```json
{
  "name": "xmeas_7_xmeas_8_ratio",
  "type": "interaction",
  "sensors": ["xmeas_7", "xmeas_8"],
  "params": {"operation": "ratio"}
}
```
- Use case: Pressure/Level ratios, Flow/Temperature ratios
- Example: Reactor pressure / Reactor level = pressure intensity

**Difference Operation:**
```json
{
  "name": "xmeas_7_xmeas_8_diff",
  "type": "interaction",
  "sensors": ["xmeas_7", "xmeas_8"],
  "params": {"operation": "difference"}
}
```
- Use case: Temperature differentials, Pressure drops
- Example: Inlet temp - Outlet temp = heat loss

**Product Operation:**
```json
{
  "name": "xmeas_7_xmeas_8_product",
  "type": "interaction",
  "sensors": ["xmeas_7", "xmeas_8"],
  "params": {"operation": "product"}
}
```
- Use case: Energy calculations (Pressure × Flow)
- Example: Reactor pressure × Flow rate = work done

**Implementation:**
```python
def apply_interaction(sensor_data, transform):
    sensors = transform['sensors']
    operation = transform['params'].get('operation', 'product')

    if operation == 'ratio':
        num = sensor_data.get(sensors[0], 0.0)
        den = sensor_data.get(sensors[1], 1.0)
        return num / (den + 1e-10)  # Avoid division by zero

    elif operation == 'difference':
        return sensor_data.get(sensors[0], 0.0) - sensor_data.get(sensors[1], 0.0)

    elif operation == 'product':
        return sensor_data.get(sensors[0], 0.0) * sensor_data.get(sensors[1], 0.0)
```

### 6.4 Statistical Transformation

**Purpose:** Normalize sensors by baseline values for anomaly sensitivity

**Definition:**
```json
{
  "name": "xmeas_1_deviation",
  "type": "statistical",
  "sensor": "xmeas_1",
  "params": {"stat_type": "deviation_from_run_mean"}
}
```

**Implementation:**
```python
def apply_statistical(sensor_data, transform, baseline_means):
    sensor = transform['sensor']
    stat_type = transform['params'].get('stat_type', 'deviation_from_run_mean')

    if stat_type == 'deviation_from_run_mean':
        baseline = baseline_means.get(sensor, 0.0)
        current = sensor_data.get(sensor, 0.0)
        return current - baseline
```

**Use Cases:**
- Anomaly detection (deviations from normal operation)
- Drift detection (sensor degradation over time)
- Process stability monitoring

**Example:**
```
Baseline (normal operation): xmeas_1 = 2700
Current reading: xmeas_1 = 2705
Deviation: +5 (slight positive drift)

During anomaly: xmeas_1 = 2850
Deviation: +150 (significant deviation detected!)
```

### 6.5 Extensibility

**Adding New Transformation Types:**

1. Define transformation schema:
```json
{
  "name": "feature_name",
  "type": "fourier_transform",
  "sensor": "xmeas_1",
  "params": {
    "window_size": 100,
    "frequency_bands": [0.1, 1.0, 10.0]
  }
}
```

2. Implement transformation function:
```python
def apply_fourier_transform(sensor_data, transform, historical_data):
    sensor = transform['sensor']
    window_size = transform['params']['window_size']
    frequency_bands = transform['params']['frequency_bands']

    # Get historical window
    signal = historical_data[sensor][-window_size:]

    # Apply FFT
    fft_values = np.fft.fft(signal)
    frequencies = np.fft.fftfreq(window_size)

    # Extract power in specified frequency bands
    power_spectrum = np.abs(fft_values) ** 2

    # Return sum of power in each band
    return [
        np.sum(power_spectrum[(frequencies >= band[0]) & (frequencies < band[1])])
        for band in frequency_bands
    ]
```

3. Register in transformation engine:
```python
TRANSFORMATION_FUNCTIONS = {
    'identity': apply_identity,
    'polynomial': apply_polynomial,
    'interaction': apply_interaction,
    'statistical': apply_statistical,
    'fourier_transform': apply_fourier_transform,  # New type
}
```

---

## 7. Integration with ML Pipeline

### 7.1 Training Phase Integration

**Jupyter Notebook Workflow:**

```python
import requests
import json
import mlflow
from sklearn.model_selection import train_test_split
import xgboost as xgb

# Step 1: Define Feature Engineering Configuration
feature_config = {
    "name": "TEP Anomaly Detection - v1.0",
    "equipment_type": "tep_reactor",
    "base_sensors": [
        "xmeas_1", "xmeas_2", ..., "xmv_11"  # 52 sensors
    ],
    "transformations": [
        {"name": "xmeas_1", "type": "identity", "sensor": "xmeas_1"},
        {"name": "xmeas_7_squared", "type": "polynomial",
         "sensor": "xmeas_7", "params": {"power": 2}},
        {"name": "xmeas_7_xmeas_8_ratio", "type": "interaction",
         "sensors": ["xmeas_7", "xmeas_8"], "params": {"operation": "ratio"}},
        # ... 65 total transformations
    ],
    "version": "1.0.0",
    "status": "active"
}

# Step 2: Register Feature Config
ML_SERVICE_URL = "http://ml-service-api:8002"
JWT_TOKEN = "your_jwt_token"

response = requests.post(
    f"{ML_SERVICE_URL}/api/feature-configs",
    headers={"Authorization": f"Bearer {JWT_TOKEN}"},
    json=feature_config
)
feature_config_id = response.json()['id']
print(f"Feature Config ID: {feature_config_id}")

# Step 3: Load and Engineer Features
raw_data = pd.read_csv('tep_data.csv')

# Apply transformations (same as production)
from feature_engineering import engineer_features
X = engineer_features(raw_data, feature_config['transformations'])
y = raw_data['is_anomaly']

# Step 4: Train Model
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3)

mlflow.start_run()
model = xgb.XGBClassifier(...)
model.fit(X_train, y_train)

# Log to MLflow
mlflow.sklearn.log_model(model, "model")
mlflow_run_id = mlflow.active_run().info.run_id

# Step 5: Register Model with Feature Config Link
model_data = {
    "model_name": "TEP XGBoost Anomaly Detector",
    "equipment_type": "tep_reactor",
    "model_type": "xgboost",
    "model_version": "1.0.0",
    "status": "production",
    "mlflow_run_id": mlflow_run_id,
    "feature_config_id": feature_config_id,  # Critical linkage!
    "accuracy": accuracy_score(y_test, y_pred),
    "precision_score": precision_score(y_test, y_pred),
    "recall": recall_score(y_test, y_pred),
    "f1_score": f1_score(y_test, y_pred),
    "auc_roc": roc_auc_score(y_test, y_proba),
    "hyperparameters": model.get_params(),
    "feature_names": feature_config['transformations'].keys()
}

response = requests.post(
    f"{ML_SERVICE_URL}/api/models",
    headers={"Authorization": f"Bearer {JWT_TOKEN}"},
    json=model_data
)
model_id = response.json()['model_id']
print(f"Model ID: {model_id}")
```

### 7.2 Production Inference Integration

**Real-Time Anomaly Detection:**

```python
import requests
import mlflow
import pandas as pd
from feature_engineering import engineer_features

# Configuration
ML_SERVICE_URL = "http://ml-service-api:8002"
MLFLOW_TRACKING_URI = "http://mlflow:5000"
JWT_TOKEN = "your_jwt_token"
MODEL_ID = "b5c051b7-e211-44c0-9783-70d6032f2a4f"

mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
headers = {"Authorization": f"Bearer {JWT_TOKEN}"}

# Step 1: Retrieve Model Metadata
response = requests.get(
    f"{ML_SERVICE_URL}/api/models/{MODEL_ID}",
    headers=headers
)
model_metadata = response.json()

# Step 2: Retrieve Feature Config
response = requests.get(
    f"{ML_SERVICE_URL}/api/feature-configs/{model_metadata['feature_config_id']}",
    headers=headers
)
feature_config = response.json()

# Step 3: Load Model from MLflow
model = mlflow.sklearn.load_model(
    f"runs:/{model_metadata['mlflow_run_id']}/model"
)

# Step 4: Process Streaming Data
def process_sensor_reading(sensor_data):
    # Apply feature engineering (identical to training!)
    feature_vector = engineer_features(
        sensor_data,
        feature_config,
        model_metadata['feature_names']
    )

    # Convert to DataFrame for sklearn
    feature_df = pd.DataFrame([feature_vector],
                              columns=model_metadata['feature_names'])

    # Run inference
    prediction = model.predict(feature_df)[0]
    probability = model.predict_proba(feature_df)[0][1]

    return {
        "prediction": int(prediction),
        "anomaly_probability": float(probability),
        "is_anomaly": prediction == 1
    }

# Step 5: Real-Time Processing
for sensor_reading in sensor_stream:
    result = process_sensor_reading(sensor_reading)

    if result['is_anomaly']:
        trigger_alert(result)
```

### 7.3 Model-Config Versioning

**Scenario:** Feature engineering evolves over time

**v1.0 Config:**
```json
{
  "version": "1.0.0",
  "transformations": [65 features]
}
```

**v2.0 Config (improved):**
```json
{
  "version": "2.0.0",
  "transformations": [80 features]  // Added 15 new features
}
```

**Model Registry:**
```
Model A:
  - feature_config_id: config_v1.0
  - status: production
  - Performance: 85% accuracy

Model B:
  - feature_config_id: config_v2.0
  - status: production
  - Performance: 89% accuracy (improved!)
```

**A/B Testing:**
```python
# 50% traffic to Model A (v1.0 features)
# 50% traffic to Model B (v2.0 features)

if user_id % 2 == 0:
    model_id = "model_a_id"
    config_id = "config_v1.0_id"
else:
    model_id = "model_b_id"
    config_id = "config_v2.0_id"

# Each model uses its own feature config
# Ensures reproducibility and fair comparison
```

---

## 8. Scalability Considerations

### 8.1 Configuration Retrieval Performance

**Current:**
- Feature config retrieval: ~10-20ms
- Includes JSONB parsing
- Connection pool reuse

**Optimization for High Throughput:**

**Option 1: In-Memory Caching**
```python
from functools import lru_cache
from datetime import timedelta
import asyncio

# Cache feature configs for 5 minutes
cache = {}
cache_ttl = timedelta(minutes=5)

async def get_cached_feature_config(config_id):
    if config_id in cache:
        config, timestamp = cache[config_id]
        if datetime.now() - timestamp < cache_ttl:
            return config

    # Cache miss - fetch from database
    config = await repository.get_config_by_id(company_id, config_id)
    cache[config_id] = (config, datetime.now())
    return config
```

**Option 2: Redis Caching**
```python
import redis
import json

redis_client = redis.Redis(host='redis', port=6379)

async def get_cached_feature_config(config_id):
    # Try Redis first
    cached = redis_client.get(f"feature_config:{config_id}")
    if cached:
        return json.loads(cached)

    # Cache miss - fetch from database
    config = await repository.get_config_by_id(company_id, config_id)

    # Cache for 5 minutes
    redis_client.setex(
        f"feature_config:{config_id}",
        300,
        json.dumps(config)
    )

    return config
```

**Performance Impact:**
- Without cache: 10-20ms per request
- With cache: <1ms per request (50-200x speedup!)
- Critical for high-throughput inference

### 8.2 Transformation Computation Performance

**Benchmark: TEP Dataset (52 sensors → 65 features)**

**Single Row:**
- Identity transforms (29): ~0.1ms
- Polynomial transforms (9): ~0.2ms
- Interaction transforms (18): ~0.3ms
- Statistical transforms (9): ~0.2ms
- **Total: ~0.8ms per sample**

**Batch Processing (1000 samples):**
- NumPy vectorization: ~50ms
- **50μs per sample (16x faster!)**

**Optimization:**
```python
import numpy as np

def engineer_features_vectorized(sensor_data_df, feature_config):
    """Vectorized feature engineering for batches"""
    features = {}

    for transform in feature_config['transformations']:
        name = transform['name']
        trans_type = transform['type']

        if trans_type == 'identity':
            features[name] = sensor_data_df[transform['sensor']].values

        elif trans_type == 'polynomial':
            sensor = transform['sensor']
            power = transform['params']['power']
            features[name] = sensor_data_df[sensor].values ** power

        elif trans_type == 'interaction':
            s1, s2 = transform['sensors']
            operation = transform['params']['operation']

            if operation == 'ratio':
                features[name] = sensor_data_df[s1] / (sensor_data_df[s2] + 1e-10)
            elif operation == 'difference':
                features[name] = sensor_data_df[s1] - sensor_data_df[s2]
            elif operation == 'product':
                features[name] = sensor_data_df[s1] * sensor_data_df[s2]

        elif trans_type == 'statistical':
            sensor = transform['sensor']
            baseline = BASELINE_MEANS.get(sensor, 0.0)
            features[name] = sensor_data_df[sensor] - baseline

    return pd.DataFrame(features)
```

### 8.3 Database Scalability

**Current Load:**
- 3 tenants
- ~10 feature configs per tenant
- ~100 model queries/day

**At Scale (1000 tenants, 100K queries/day):**

**Database Optimizations:**
```sql
-- Index for frequent queries
CREATE INDEX idx_feature_configs_composite
ON feature_engineering_configs(equipment_type, status, created_at DESC);

-- Partial index for active configs only
CREATE INDEX idx_feature_configs_active
ON feature_engineering_configs(equipment_type, created_at DESC)
WHERE status = 'active';

-- JSONB GIN index for transformation searches
CREATE INDEX idx_feature_configs_transformations_gin
ON feature_engineering_configs USING gin(transformations);
```

**Connection Pool Tuning:**
```python
# High-throughput configuration
db_pool = asyncpg.create_pool(
    database='industryflow',
    user='ml_service_user',
    min_size=10,       # Increased from 5
    max_size=50,       # Increased from 20
    max_inactive_connection_lifetime=300,
    command_timeout=30
)
```

**Read Replicas:**
```
Write Operations (POST, PUT):
  → Primary Database

Read Operations (GET):
  → Read Replica 1
  → Read Replica 2
  → Read Replica 3 (load balanced)
```

### 8.4 Horizontal Scaling

**Current:** Single ML Service instance

**Scaled Architecture:**
```
Load Balancer (nginx)
├─→ ML Service Instance 1
│   ├─→ Feature Config Cache (Redis)
│   └─→ DB Pool (10-50 connections)
│
├─→ ML Service Instance 2
│   ├─→ Feature Config Cache (Redis)
│   └─→ DB Pool (10-50 connections)
│
└─→ ML Service Instance 3
    ├─→ Feature Config Cache (Redis)
    └─→ DB Pool (10-50 connections)
            │
            ▼
    TimescaleDB Primary
    ├─→ Read Replica 1
    ├─→ Read Replica 2
    └─→ Read Replica 3
```

**Performance Targets:**
- Config retrieval: <10ms (p95)
- Feature engineering (single): <1ms
- Feature engineering (batch 1000): <100ms
- End-to-end inference: <50ms (p95)

---

## End of Feature Engineering Service Documentation

**Key Architectural Principles:**

1. **Reproducibility:** Identical transformations from training to production
2. **Versioning:** Feature configs versioned alongside models
3. **Extensibility:** Easy to add new transformation types
4. **Performance:** Optimized for high-throughput real-time inference
5. **Isolation:** Multi-tenant at schema level with foreign key integrity
6. **Integration:** Seamless linkage with ML models via feature_config_id
7. **Scalability:** Caching, vectorization, and horizontal scaling ready
