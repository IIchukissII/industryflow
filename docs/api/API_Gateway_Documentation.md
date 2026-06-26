<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow API Gateway - Technical Documentation

**Service:** API Gateway
**Version:** 1.0.0
**Port:** 8000
**Architecture:** Schema-per-tenant (v5.0)
**Date:** November 2025

---

## Overview

REST API service providing multi-tenant access to industrial IoT sensor data, equipment management, alert configuration, and ML model operations. Implements schema-per-tenant architecture with automatic query routing via PostgreSQL `search_path`. Service handles 40+ endpoints with JWT-based authentication and role-based authorization.

**Technical Specifications:**
- Workers: 8 uvicorn processes
- Database Connections: 150 max (18 per worker + 2 overflow)
- Authentication: JWT tokens (7-day expiration)
- Port: 8000
- Protocol: HTTP/REST + WebSocket

---

## Architecture Overview

### Schema-Per-Tenant Model

Each tenant company has dedicated PostgreSQL schema:
- `tenant_550e8400_e29b_41d4_a716_446655440000` (ACME Manufacturing)
- `tenant_550e8400_e29b_41d4_a716_446655440001` (TechCorp Industries)  
- `tenant_550e8400_e29b_41d4_a716_446655440002` (Global Systems Inc)

Shared `public` schema contains:
- `companies` table (tenant registry)
- `user` table (authentication)

### Request Flow

1. User authenticates → JWT token with `company_id`
2. Request reaches API Gateway → extracts `company_id` from token
3. Database session created → `SET search_path TO tenant_{uuid}, public`
4. Queries automatically route to correct tenant schema
5. Response returned with tenant-specific data

**Advantages over RLS:**
- Compatible with TimescaleDB compression (RLS is not)
- Better query performance (no policy evaluation overhead)
- Simpler permission model
- Used by Salesforce, AWS RDS, other production systems

---

## Service Configuration

### Docker Compose Entry

```yaml
api-gateway:
  container_name: industryflow-api-gateway
  build:
    context: ./services/api_gateway
    dockerfile: Dockerfile
  ports:
    - "8000:8000"
  environment:
    DB_HOST: timescaledb
    DB_PORT: 5432
    DB_NAME: industryflow
    API_GATEWAY_DB_USER: api_gateway_user
    API_GATEWAY_DB_PASSWORD: ${API_GATEWAY_DB_PASSWORD}
    REDIS_HOST: redis
    REDIS_PORT: 6379
    REDIS_DB: 0
    KAFKA_BOOTSTRAP_SERVERS: kafka:29092
    KAFKA_TOPIC_SENSOR_DATA: sensor-data-raw
    jwt_secret_key: ${JWT_SECRET_KEY}
  command: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 8
  depends_on:
    - timescaledb
    - redis
    - kafka
  networks:
    - industryflow-network
  restart: unless-stopped
```

### Database Configuration

**Connection Pool Settings:**
```python
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=18,        # Per worker
    max_overflow=2,      # Extra connections
    pool_pre_ping=True   # Connection validation
)
```

**Calculation:**
- 8 workers × 18 pool_size = 144 connections
- 8 workers × 2 max_overflow = 16 overflow
- Total capacity: 160 connections
- Role allocation: 150 connections
- Headroom: 6 connections for maintenance

**Database Role:**
```sql
CREATE ROLE api_gateway_user WITH LOGIN PASSWORD '***';
ALTER ROLE api_gateway_user CONNECTION LIMIT 150;
ALTER ROLE api_gateway_user SET statement_timeout = '30s';
GRANT CREATE ON SCHEMA public TO api_gateway_user;
GRANT USAGE ON SCHEMA public TO api_gateway_user;
GRANT SELECT ON public.companies TO api_gateway_user;
GRANT SELECT ON public."user" TO api_gateway_user;
```

---

## API Endpoints

### Authentication Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| POST | `/auth/jwt/login` | JWT token authentication | None |
| POST | `/auth/register` | User registration | None |
| POST | `/auth/jwt/logout` | Token invalidation | Bearer Token |
| GET | `/users/me` | Get current user info | Bearer Token |
| GET | `/api/users` | List all users | Admin only |

**Login Request:**
```bash
curl -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@acme.com&password=SecurePass123!"
```

**Login Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Register Request:**
```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@acme.com",
    "password": "SecurePass123!",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "role": "engineer"
  }'
```

**Register Response:**
```json
{
  "id": "bd6a8a44-e018-4042-8539-c84cf6715921",
  "email": "test@acme.com",
  "is_active": true,
  "is_superuser": false,
  "is_verified": false,
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "role": "engineer"
}
```

**Get Current User:**
```bash
curl -X GET http://localhost:8000/users/me \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
{
  "id": "bd6a8a44-e018-4042-8539-c84cf6715921",
  "email": "test@acme.com",
  "is_active": true,
  "is_superuser": false,
  "is_verified": false,
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "role": "engineer"
}
```

### Measurement Endpoints

**Note:** Data ingestion is handled by the separate **Ingestion Service** on port 8003. See Ingestion Service documentation for data ingestion endpoints.

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/measurements` | Get raw measurements | Bearer Token |
| GET | `/api/measurements/latest` | Latest value per sensor | Bearer Token |
| GET | `/api/measurements/{sensor_id}` | Sensor-specific measurements | Bearer Token |

**Query Measurements:**
```bash
curl -X GET "http://localhost:8000/api/measurements?limit=5" \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
[]
```
*Empty array when no streaming data available*

**Query Parameters:**
- `sensor_id` (UUID): Filter by sensor
- `equipment_id` (UUID): Filter by equipment
- `limit` (int): Max results (1-1000, default 100)

**Response Structure (when data exists):**
```json
[
  {
    "time": "2025-11-08T00:00:00Z",
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "site_id": "Building A - Floor 1",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "value": 45.67,
    "unit": "°C",
    "quality_code": 1
  }
]
```

### Aggregation Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/aggregations/{window}` | Get aggregated statistics | Bearer Token |
| GET | `/api/aggregations/{window}/latest` | Latest aggregation per sensor | Bearer Token |
| GET | `/api/aggregations/combined/{sensor_id}` | All timeframes for sensor | Bearer Token |

**Valid Windows:**
- `1min`: 1-minute aggregations
- `5min`: 5-minute aggregations
- `1hour`: 1-hour aggregations

**Query Aggregations:**
```bash
curl -X GET "http://localhost:8000/api/aggregations/1min?limit=5" \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
[]
```
*Empty array when no aggregated data available*

**Response Structure (when data exists):**
```json
[
  {
    "time": "2025-11-08T00:00:00Z",
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "site_id": "Building A - Floor 1",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "avg_value": 45.67,
    "min_value": 42.0,
    "max_value": 48.5,
    "count_values": 60,
    "unit": "°C"
  }
]
```

### Equipment Management Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/equipment` | List all equipment | Bearer Token |
| GET | `/api/equipment/{equipment_id}` | Get specific equipment | Bearer Token |
| POST | `/api/equipment` | Create equipment | Bearer Token |
| PUT | `/api/equipment/{equipment_id}` | Update equipment | Bearer Token |
| DELETE | `/api/equipment/{equipment_id}` | Delete equipment | Bearer Token |
| GET | `/api/equipment/{equipment_id}/sensors` | Get equipment sensors | Bearer Token |
| POST | `/api/equipment/{equipment_id}/sensors` | Add sensor | Bearer Token |
| DELETE | `/api/equipment/{equipment_id}/sensors/{sensor_id}` | Remove sensor | Bearer Token |
| POST | `/api/equipment/{equipment_id}/sensors/bulk` | Bulk add sensors | Bearer Token |

**List Equipment:**
```bash
curl -X GET http://localhost:8000/api/equipment \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
[
  {
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "equipment_type": "centrifugal_pump",
    "name": "Main Production Pump",
    "description": null,
    "site_id": null,
    "location": "Building A - Floor 1",
    "sensor_count": 52,
    "batch_timeout_seconds": 5,
    "require_complete_batch": true,
    "min_sensors_for_partial": null,
    "status": "active",
    "commissioned_date": null,
    "last_maintenance_date": null,
    "next_maintenance_date": null,
    "created_at": "2025-11-08T00:58:56.074954+00:00",
    "updated_at": "2025-11-08T00:58:56.074954+00:00",
    "created_by": null,
    "expected_sensors": []
  }
]
```

**Create Equipment:**
```bash
curl -X POST http://localhost:8000/api/equipment \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "equipment_id": "123e4567-e89b-12d3-a456-426614174000",
    "equipment_type": "motor",
    "name": "Test Motor",
    "location": "Test Lab",
    "sensor_count": 3
  }'
```

**Response:**
```json
{
  "equipment_id": "123e4567-e89b-12d3-a456-426614174000",
  "equipment_type": "motor",
  "name": "Test Motor",
  "description": null,
  "site_id": null,
  "location": "Test Lab",
  "sensor_count": 3,
  "batch_timeout_seconds": 5,
  "require_complete_batch": true,
  "min_sensors_for_partial": null,
  "status": "active",
  "commissioned_date": null,
  "last_maintenance_date": null,
  "next_maintenance_date": null,
  "created_at": "2025-11-08T10:54:22.777597+00:00",
  "updated_at": "2025-11-08T10:54:22.777597+00:00",
  "created_by": null,
  "expected_sensors": []
}
```

**Delete Equipment:**
```bash
curl -X DELETE http://localhost:8000/api/equipment/123e4567-e89b-12d3-a456-426614174000 \
  -H "Authorization: Bearer {token}"
```

**Response:** No content (204)

**Get Equipment Sensors:**
```bash
curl -X GET http://localhost:8000/api/equipment/550e8400-e29b-41d4-a716-446655440010/sensors \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
[
  {
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
    "sensor_name": "Temperature_Sensor_1",
    "sensor_type": "temperature",
    "description": null,
    "unit": "°C",
    "min_value": null,
    "max_value": null,
    "normal_min": null,
    "normal_max": null,
    "position": 0,
    "is_critical": false,
    "is_required_for_ml": true,
    "status": "active",
    "is_active": true,
    "created_at": "2025-11-08T01:11:59.276005+00:00",
    "updated_at": "2025-11-08T01:11:59.276005+00:00"
  }
]
```

### Alert Management Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/alerts` | List alerts | Bearer Token |
| GET | `/api/alerts/unacknowledged` | Unacknowledged alerts | Bearer Token |
| GET | `/api/alerts/critical` | Critical severity alerts | Bearer Token |
| PATCH | `/api/alerts/{alert_id}/acknowledge` | Acknowledge alert | Bearer Token |

**List Alerts:**
```bash
curl -X GET "http://localhost:8000/api/alerts?limit=5" \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
[]
```
*Empty array when no alerts triggered*

**Query Parameters:**
- `sensor_id` (UUID): Filter by sensor
- `equipment_id` (UUID): Filter by equipment
- `severity` (enum): info, low, medium, high, critical
- `acknowledged` (bool): Filter by acknowledgment status
- `limit` (int): Max results (default 100, max 1000)

### Alert Rules Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/alert-rules` | List alert rules | Bearer Token |
| GET | `/api/alert-rules/{rule_id}` | Get specific rule | Bearer Token |
| POST | `/api/alert-rules` | Create rule | Bearer Token |
| PUT | `/api/alert-rules/{rule_id}` | Update rule | Bearer Token |
| DELETE | `/api/alert-rules/{rule_id}` | Delete rule | Bearer Token |
| PATCH | `/api/alert-rules/{rule_id}/detection-mode` | Switch detection mode | Bearer Token |

**List Alert Rules:**
```bash
curl -X GET http://localhost:8000/api/alert-rules \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
[
  {
    "rule_id": "7fd78efb-2587-4dc6-970d-fed95308663b",
    "name": "High Temperature Alert",
    "description": null,
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "equipment_id": null,
    "sensor_pattern": null,
    "site_id": null,
    "detection_type": "threshold",
    "condition": null,
    "threshold": 80.0,
    "threshold_min": null,
    "threshold_max": null,
    "model_id": null,
    "anomaly_threshold": 0.7,
    "model_config": null,
    "requires_complete_batch": false,
    "min_batch_completeness": 1.0,
    "severity": "high",
    "priority": 3,
    "enabled": true,
    "created_at": "2025-11-08T01:22:42.721575+00:00",
    "updated_at": "2025-11-08T01:22:42.721575+00:00",
    "created_by": null
  }
]
```

**Create Alert Rule:**
```bash
curl -X POST http://localhost:8000/api/alert-rules \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Alert Rule",
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "detection_type": "threshold",
    "threshold": 90.0,
    "severity": "critical",
    "enabled": true
  }'
```

**Response:**
```json
{
  "message": "Alert rule created successfully",
  "rule": {
    "rule_id": "69c34497-46e8-4dbe-83bb-ce7a4da0a3a3",
    "name": "Test Alert Rule",
    "description": null,
    "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
    "equipment_id": null,
    "sensor_pattern": null,
    "site_id": null,
    "detection_type": "threshold",
    "condition": null,
    "threshold": 90.0,
    "threshold_min": null,
    "threshold_max": null,
    "model_id": null,
    "anomaly_threshold": 0.7,
    "model_config": null,
    "requires_complete_batch": false,
    "min_batch_completeness": 1.0,
    "severity": "critical",
    "priority": 3,
    "enabled": true,
    "created_at": "2025-11-08T10:56:32.222367+00:00",
    "updated_at": "2025-11-08T10:56:32.222367+00:00",
    "created_by": null
  }
}
```

**Update Alert Rule:**
```bash
curl -X PUT http://localhost:8000/api/alert-rules/69c34497-46e8-4dbe-83bb-ce7a4da0a3a3 \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "threshold": 95.0,
    "severity": "high"
  }'
```

**Response:**
```json
{
  "message": "Alert rule updated successfully",
  "rule": {
    "rule_id": "69c34497-46e8-4dbe-83bb-ce7a4da0a3a3",
    "name": "Test Alert Rule",
    "threshold": 95.0,
    "severity": "high",
    "updated_at": "2025-11-08T10:56:59.797577+00:00"
  }
}
```

**Delete Alert Rule:**
```bash
curl -X DELETE http://localhost:8000/api/alert-rules/69c34497-46e8-4dbe-83bb-ce7a4da0a3a3 \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
{
  "message": "Alert rule deleted successfully",
  "deleted_rule": {
    "rule_id": "69c34497-46e8-4dbe-83bb-ce7a4da0a3a3",
    "name": "Test Alert Rule"
  }
}
```

**Detection Types:**
- `threshold`: Simple threshold comparison
- `ml_anomaly`: ML model-based detection
- `statistical`: Statistical anomaly detection

### ML Models Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/ml-models` | List ML models | Bearer Token |
| GET | `/api/ml-models/{model_id}` | Get specific model | Bearer Token |
| POST | `/api/ml-models` | Create model entry | Bearer Token |
| PUT | `/api/ml-models/{model_id}` | Update model | Bearer Token |
| DELETE | `/api/ml-models/{model_id}` | Delete model | Bearer Token |
| POST | `/api/ml-models/train` | Initiate training | Bearer Token |

**List ML Models:**
```bash
curl -X GET http://localhost:8000/api/ml-models \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
[]
```
*Empty array when no models exist*

### Training Data Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/training-data/equipment/{equipment_id}` | JSON training data | Bearer Token |
| GET | `/api/training-data/equipment/{equipment_id}/stream` | CSV streaming | Bearer Token |

**Query Parameters:**
- `lookback_days` (int): Historical days (1-365, default 30)
- `min_quality` (float): Quality threshold (0.0-1.0, default 0.8)
- `limit` (int): Max rows for JSON (1-100000, default 10000)

### Company Management Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/companies` | List companies | Admin only |
| GET | `/api/companies/{company_id}` | Get company | Admin only |
| POST | `/api/companies` | Create company | Admin only |
| PUT | `/api/companies/{company_id}` | Update company | Admin only |
| DELETE | `/api/companies/{company_id}` | Delete company | Admin only |

**List Companies (Admin):**
```bash
curl -X GET http://localhost:8000/api/companies \
  -H "Authorization: Bearer {admin-token}"
```

**Non-Admin Response:**
```json
{
  "detail": "Required role: admin"
}
```

**Admin Response:**
```json
[
  {
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "company_name": "ACME Manufacturing",
    "is_active": true,
    "created_at": "2025-11-07T00:00:00Z"
  },
  {
    "company_id": "550e8400-e29b-41d4-a716-446655440001",
    "company_name": "TechCorp Industries",
    "is_active": true,
    "created_at": "2025-11-07T00:00:00Z"
  },
  {
    "company_id": "550e8400-e29b-41d4-a716-446655440002",
    "company_name": "Global Systems Inc",
    "is_active": true,
    "created_at": "2025-11-07T00:00:00Z"
  }
]
```

**Note:** Companies table in `public` schema, accessible only to admin users.

### WebSocket Endpoints

| Protocol | Endpoint | Description | Authentication |
|----------|----------|-------------|----------------|
| WS | `/ws/sensors?token={jwt}` | Real-time sensor updates | JWT in query param |
| WS | `/ws/sensors/{equipment_id}?token={jwt}` | Equipment-specific updates | JWT in query param |

**Connection Flow:**
1. Obtain JWT token from `/auth/jwt/login`
2. Connect WebSocket with token: `ws://localhost:8000/ws/sensors?token={jwt}`
3. Server validates token and extracts `company_id`
4. Server filters Redis cache by `company_id`
5. Client receives real-time updates every 1 second

**Message Format:**
```json
{
  "type": "sensor_update",
  "timestamp": 1699401234.567,
  "sensors": {
    "550e8400-e29b-41d4-a716-446655440020": {
      "value": 45.67,
      "timestamp": "2025-11-08T00:00:00Z",
      "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
      "site_id": "Building A - Floor 1",
      "company_id": "550e8400-e29b-41d4-a716-446655440000",
      "unit": "°C",
      "quality_code": 1
    }
  },
  "count": 1
}
```

### Cache Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/api/cache/sensors` | Get cached sensor values | Bearer Token |

**Get Cached Sensors:**
```bash
curl -X GET http://localhost:8000/api/cache/sensors \
  -H "Authorization: Bearer {token}"
```

**Response:**
```json
{
  "cached_sensors": 0,
  "sensors": {},
  "company_id": "550e8400-e29b-41d4-a716-446655440000"
}
```
*Empty when no streaming data active*

### Health & System Endpoints

| Method | Endpoint | Description | Authorization |
|--------|----------|-------------|---------------|
| GET | `/` | API root info | None |
| GET | `/health` | Service health check | None |
| GET | `/docs` | Swagger UI | None |
| GET | `/redoc` | ReDoc documentation | None |

**Health Check:**
```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "timestamp": "2025-11-08T10:12:40.096464"
}
```

**API Root:**
```bash
curl http://localhost:8000/
```

**Response:**
```json
{
  "name": "IndustryFlow API",
  "version": "1.0.0",
  "description": "Real-time IoT Data Processing Platform",
  "docs_url": "/docs",
  "architecture": "Schema-per-tenant (v5.0)",
  "endpoints": {
    "health": "/health",
    "auth_login": "/auth/jwt/login",
    "auth_register": "/auth/register",
    "users_me": "/users/me",
    "measurements": "/api/measurements",
    "measurements_latest": "/api/measurements/latest",
    "aggregations_1min": "/api/aggregations/1min",
    "aggregations_5min": "/api/aggregations/5min",
    "aggregations_1hour": "/api/aggregations/1hour",
    "websocket_sensors": "/ws/sensors",
    "cache_sensors": "/api/cache/sensors",
    "training_data": "/api/training-data/equipment/{equipment_id}",
    "alerts": "/api/alerts",
    "alert_rules": "/api/alert-rules",
    "ml_models": "/api/ml-models",
    "companies": "/api/companies",
    "equipment": "/api/equipment"
  }
}
```

---

## Database Schema Routing

### Dependency Injection

```python
from dependencies import get_db_with_tenant

@router.get("/sensors")
async def get_sensors(
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    # Search path automatically set: SET search_path TO tenant_{uuid}, public
    result = await db.execute(text("SELECT * FROM sensors"))
```

### Schema Name Conversion

```python
def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert company_id UUID to schema name.
    
    Example: 
        '550e8400-e29b-41d4-a716-446655440000' 
        -> 'tenant_550e8400_e29b_41d4_a716_446655440000'
    """
    schema_uuid = str(company_id).replace("-", "_")
    return f"tenant_{schema_uuid}"
```

### Routing Implementation

```python
async def get_db_with_tenant(
    current_user: User = Depends(current_active_user)
) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            if current_user and current_user.company_id:
                schema_name = normalize_company_id_to_schema(current_user.company_id)
                await session.execute(
                    text(f"SET search_path TO {schema_name}, public")
                )
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise
        finally:
            await session.close()
```

### Query Resolution Rules

With `search_path TO tenant_{uuid}, public` set:

**Tenant-specific tables** (automatic routing):
```sql
SELECT * FROM sensors WHERE equipment_id = 'pump-001';
-- Resolves to: tenant_550e8400_e29b_41d4_a716_446655440000.sensors
```

**Shared tables** (fallback to public):
```sql
SELECT * FROM companies WHERE company_id = '{uuid}';
-- Resolves to: public.companies
```

**Explicit schema reference**:
```sql
SELECT * FROM "user" WHERE email = 'user@example.com';
-- Resolves to: public.user
```

---

## File Structure

```
services/api_gateway/
├── main.py                          # FastAPI application entry point
├── config.py                        # Configuration management
├── database.py                      # Database connection pools
├── dependencies.py                  # Dependency injection functions
├── schemas.py                       # Pydantic request/response models
├── users.py                         # FastAPI-users integration
├── Dockerfile                       # Container build definition
├── requirements.txt                 # Python package dependencies
│
├── models/
│   ├── __init__.py
│   ├── user.py                      # User authentication model
│   └── schemas.py                   # SQLAlchemy ORM models
│
├── messaging/
│   ├── __init__.py
│   ├── kafka_producer.py            # Kafka message producer
│   ├── redis_client.py              # Redis connection client
│   └── cache_updater.py             # Background cache update task
│
└── routers/
    ├── __init__.py                  # Router module exports
    ├── health.py                    # Health check endpoint
    ├── measurements.py              # Sensor measurement queries
    ├── aggregations.py              # Time-series aggregations
    ├── equipment.py                 # Equipment CRUD operations
    ├── companies.py                 # Company management (admin)
    ├── alerts_history.py            # Alert history queries
    ├── alert_rules.py               # Alert rule configuration
    ├── ml_models.py                 # ML model metadata management
    ├── training_data.py             # Historical data export
    ├── ingestion.py                 # Sensor data ingestion
    ├── websocket.py                 # Real-time data streaming
    └── cache.py                     # Redis cache inspection
```

---

## Equipment-Sensors Data Model

Direct ownership relationship without junction table:

```sql
CREATE TABLE sensors (
    sensor_id UUID PRIMARY KEY,
    equipment_id UUID REFERENCES equipment(equipment_id),
    sensor_type VARCHAR(50),
    unit VARCHAR(20),
    description TEXT,
    is_active BOOLEAN
);
```

Query pattern:
```sql
SELECT * FROM sensors WHERE equipment_id = '550e8400-e29b-41d4-a716-446655440010';
```

---

## Redis Cache Updater

Background task updating Redis cache every 2 seconds with latest sensor values from all tenant schemas.

### Implementation

```python
async def update_redis_cache():
    while True:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # Query all tenant schemas
            tenant_schemas = await conn.fetch("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name LIKE 'tenant_%'
            """)
            
            for schema_row in tenant_schemas:
                schema_name = schema_row['schema_name']
                company_id = schema_name.replace('tenant_', '').replace('_', '-')
                
                # Query latest values from each tenant
                query = f"""
                    SELECT DISTINCT ON (sensor_id)
                        time, sensor_id, equipment_id, site_id,
                        value, unit, quality_code
                    FROM {schema_name}.sensor_measurements
                    WHERE time > NOW() - INTERVAL '1 hour'
                    ORDER BY sensor_id, time DESC
                    LIMIT 1000
                """
                
                rows = await conn.fetch(query)
                
                # Update Redis with company_id metadata
                for row in rows:
                    await redis_client.set_sensor_value(
                        sensor_id=str(row['sensor_id']),
                        value=float(row['value']),
                        metadata={
                            "company_id": company_id,
                            "equipment_id": str(row['equipment_id']),
                            ...
                        },
                        ttl=60
                    )
        
        await asyncio.sleep(2)
```

### Cache Access Pattern

WebSocket and cache endpoints filter Redis data by `company_id`:

```python
all_sensors = await redis_client.get_all_sensors()
company_sensors = {
    sensor_id: data 
    for sensor_id, data in all_sensors.items()
    if data.get('company_id') == str(current_user.company_id)
}
```

---

## Environment Variables

### Required Variables

```bash
# Database
DB_HOST=timescaledb
DB_PORT=5432
DB_NAME=industryflow
API_GATEWAY_DB_USER=api_gateway_user
API_GATEWAY_DB_PASSWORD=<base64-encoded>

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:29092
KAFKA_TOPIC_SENSOR_DATA=sensor-data-raw

# JWT
JWT_SECRET_KEY=<base64-encoded>

# Service
API_GATEWAY_PORT=8000
API_GATEWAY_HOST=0.0.0.0
```

### Variable Usage in Code

**config.py:**
```python
class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    API_GATEWAY_DB_USER: str
    API_GATEWAY_DB_PASSWORD: str
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    KAFKA_BOOTSTRAP_SERVERS: str
    KAFKA_TOPIC_SENSOR_DATA: str
    jwt_secret_key: str
    
    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.API_GATEWAY_DB_USER}:{self.API_GATEWAY_DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
```

---

## Service Verification

### Health Check

```bash
curl http://localhost:8000/health
```

Response format:
```json
{
  "status": "healthy",
  "database": "connected",
  "timestamp": "2025-11-08T10:12:40.096464"
}
```

### Authentication Test

```bash
# Register user
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "SecurePass123!",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "role": "engineer"
  }'

# Login
curl -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com&password=SecurePass123!"
```

### Data Query Test

```bash
TOKEN="your-jwt-token-here"

curl -X GET "http://localhost:8000/api/measurements?limit=10" \
  -H "Authorization: Bearer ${TOKEN}"
```

### Database Connections

```sql
SELECT 
    usename, 
    application_name, 
    COUNT(*) as connections,
    state
FROM pg_stat_activity 
WHERE datname = 'industryflow'
GROUP BY usename, application_name, state
ORDER BY connections DESC;
```

Expected result:
```
    usename         | application_name | connections |  state
--------------------+------------------+-------------+---------
 api_gateway_user   | uvicorn          |    144      | idle
```

### Schema Routing Verification

```sql
-- Verify search_path configuration
SHOW search_path;

-- Result format: tenant_550e8400_e29b_41d4_a716_446655440000, public
```

---

## Performance Characteristics

### Connection Pool Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| Workers | 8 | Uvicorn multiprocessing |
| Pool Size (per worker) | 18 | Primary connections |
| Max Overflow (per worker) | 2 | Emergency connections |
| Total Connections | 144 | 8 × 18 (normal operation) |
| Maximum Capacity | 160 | 8 × (18 + 2) |
| Role Limit | 150 | Database-enforced maximum |
| Statement Timeout | 30s | Query execution limit |

### Response Time Benchmarks

| Endpoint | Response Time |
|----------|---------------|
| `/health` | <100ms |
| `/api/measurements?limit=100` | <500ms |
| `/api/aggregations/1min?limit=100` | <300ms |
| `/auth/jwt/login` | <200ms |
| `/api/equipment` | <400ms |

### Throughput Capacity

- Concurrent Request Capacity: 150 connections
- Recommended Operating Load: 100-120 concurrent requests (80% capacity)
- Requests per Second: 200-300 req/s (query-dependent)

---

## Known Limitations

### Redis Cache Latency

Cache updates occur every 2 seconds across all tenant schemas. WebSocket clients experience 2-second maximum latency for sensor updates.

### Docker Healthcheck Timing

Healthcheck reports "unhealthy" status during 8-worker startup despite functional service. Health endpoint `/health` confirms actual service status.

### Shared User Table

User authentication table resides in `public` schema, accessible to all tenants. User data isolation relies on application-level filtering by `company_id`.

---

## Troubleshooting Guide

### Service Won't Start

**Symptom:** Container exits immediately or restarts continuously.

**Check:**
1. Environment variables: `docker-compose config`
2. Database connectivity: `docker exec -it industryflow-timescaledb psql -U postgres`
3. Logs: `docker logs industryflow-api-gateway`

**Common Causes:**
- Missing environment variable (e.g., `REDIS_DB`)
- Database role doesn't exist
- Database permissions insufficient
- Port 8000 already in use

### Database Permission Denied

**Symptom:** `permission denied for schema tenant_xxx`

**Fix:**
```sql
GRANT USAGE ON SCHEMA tenant_xxx TO api_gateway_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA tenant_xxx TO api_gateway_user;
```

### Connection Pool Exhausted

**Symptom:** `connection pool exhausted` errors in logs.

**Analysis:**
```sql
SELECT COUNT(*) FROM pg_stat_activity 
WHERE usename = 'api_gateway_user';
```

**Fix:**
- Increase `pool_size` in `database.py`
- Increase `CONNECTION LIMIT` on role
- Reduce number of workers

### JWT Token Invalid

**Symptom:** `401 Unauthorized` on authenticated endpoints.

**Check:**
1. Token not expired (7-day lifetime)
2. JWT secret matches between services
3. Token format: `Authorization: Bearer {token}`

**Debug:**
```python
from jose import jwt
decoded = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
print(decoded)
```

### Schema Routing Not Working

**Symptom:** Queries return empty results despite data in tenant schema.

**Debug:**
```sql
-- Check current search_path
SHOW search_path;

-- Manually set and test
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;
SELECT * FROM sensors LIMIT 1;
```

**Fix:** Verify `get_db_with_tenant` dependency is used, not `get_db`.

---

## Appendix A: Complete Endpoint Reference

### Authentication & Users

```
POST   /auth/jwt/login                    # Login
POST   /auth/jwt/logout                   # Logout
POST   /auth/register                     # Register
GET    /users/me                          # Current user
PATCH  /users/me                          # Update current user
GET    /api/users                         # List all users (admin)
```

### Measurements

**Note:** For sensor data ingestion, use the **Ingestion Service** (port 8003): `POST /ingest`

```
GET    /api/measurements                  # List measurements
GET    /api/measurements/latest           # Latest per sensor
GET    /api/measurements/{sensor_id}      # Sensor-specific
```

### Aggregations

```
GET    /api/aggregations/{window}         # Get aggregations
GET    /api/aggregations/{window}/latest  # Latest per sensor
GET    /api/aggregations/combined/{sensor_id}  # All timeframes
```

### Equipment Management

```
GET    /api/equipment                                        # List equipment
GET    /api/equipment/{equipment_id}                         # Get equipment
POST   /api/equipment                                        # Create equipment
PUT    /api/equipment/{equipment_id}                         # Update equipment
DELETE /api/equipment/{equipment_id}                         # Delete equipment
GET    /api/equipment/{equipment_id}/sensors                 # List sensors
POST   /api/equipment/{equipment_id}/sensors                 # Add sensor
DELETE /api/equipment/{equipment_id}/sensors/{sensor_id}     # Remove sensor
POST   /api/equipment/{equipment_id}/sensors/bulk            # Bulk add sensors
```

### Alerts

```
GET    /api/alerts                        # List alerts
GET    /api/alerts/unacknowledged         # Unacknowledged
GET    /api/alerts/critical               # Critical alerts
PATCH  /api/alerts/{alert_id}/acknowledge # Acknowledge
```

### Alert Rules

```
GET    /api/alert-rules                   # List rules
GET    /api/alert-rules/{rule_id}         # Get rule
POST   /api/alert-rules                   # Create rule
PUT    /api/alert-rules/{rule_id}         # Update rule
DELETE /api/alert-rules/{rule_id}         # Delete rule
PATCH  /api/alert-rules/{rule_id}/detection-mode  # Switch mode
```

### ML Models

```
GET    /api/ml-models                     # List models
GET    /api/ml-models/{model_id}          # Get model
POST   /api/ml-models                     # Create model
PUT    /api/ml-models/{model_id}          # Update model
DELETE /api/ml-models/{model_id}          # Delete model
POST   /api/ml-models/train               # Train model
```

### Training Data

```
GET    /api/training-data/equipment/{equipment_id}          # JSON
GET    /api/training-data/equipment/{equipment_id}/stream   # CSV stream
```

### Company Management

```
GET    /api/companies                     # List companies (admin)
GET    /api/companies/{company_id}        # Get company (admin)
POST   /api/companies                     # Create (admin)
PUT    /api/companies/{company_id}        # Update (admin)
DELETE /api/companies/{company_id}        # Delete (admin)
```

### WebSocket

```
WS     /ws/sensors?token={jwt}            # All sensors
WS     /ws/sensors/{equipment_id}?token={jwt}  # Equipment-specific
```

### Cache & System

```
GET    /api/cache/sensors                 # Cached values
GET    /                                  # API info
GET    /health                            # Health check
GET    /docs                              # Swagger UI
GET    /redoc                             # ReDoc
```

---

## Appendix B: Database Role Summary

| Role | Connections | Timeout | Schemas | Purpose |
|------|-------------|---------|---------|---------|
| `api_gateway_user` | 150 | 30s | All tenant + public | REST API access |
| `spark_streaming_user` | 50 | 120s | All tenant | Streaming writes |
| `alert_service_user` | 10 | 60s | All tenant | Alert detection |
| `ml_service_user` | 10 | 60s | All tenant | Model training |
| `mlflow_user` | 20 | 30s | MLflow DB only | Experiment tracking |

---

**END OF DOCUMENT**