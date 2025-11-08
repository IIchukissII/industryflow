# IndustryFlow API Gateway - Technical Documentation

**Project:** IndustryFlow v2 - Multi-Tenant IoT Platform  
**Component:** API Gateway Service  
**Architecture:** Schema-per-tenant (v5.0)  
**Author:** Konstantin (Neoversity Master's Thesis)

---

## Overview

REST API service providing multi-tenant access to industrial IoT sensor data, equipment management, alert configuration, and ML model operations. Implements schema-per-tenant architecture with automatic query routing via PostgreSQL `search_path`. Service handles 40+ endpoints with JWT-based authentication and role-based authorization.

**Technical Specifications:**
- Workers: 8 uvicorn processes
- Database Connections: 150 max (18 per worker + 2 overflow)
- Authentication: JWT tokens (30-minute expiration)
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
```

---

## API Endpoints

### Authentication Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/jwt/login` | JWT token authentication |
| POST | `/auth/register` | User registration |
| POST | `/auth/jwt/logout` | Token invalidation |
| GET | `/users/me` | Get current user info |
| GET | `/api/users` | List all users (admin) |

**Authentication Flow:**
1. POST credentials to `/auth/jwt/login`
2. Receive JWT token with `company_id` claim
3. Include token in `Authorization: Bearer {token}` header
4. API Gateway extracts `company_id` and sets schema routing

### Data Ingestion Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| POST | `/api/ingest/sensor-data` | Ingest sensor measurements | None (Kafka) |
| GET | `/api/ingest/stats` | Ingestion statistics | None |

**Request Body Example:**
```json
{
  "timestamp": "2025-11-08T00:00:00Z",
  "sensor_id": "sensor-001",
  "equipment_id": "pump-001",
  "site_id": "factory-1",
  "value": 45.67,
  "unit": "°C",
  "quality_code": 1
}
```

**Note:** `company_id` automatically assigned from authenticated user's token.

### Measurement Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/measurements` | Get raw measurements | `tenant_{uuid}.sensor_measurements` |
| GET | `/api/measurements/latest` | Latest value per sensor | `tenant_{uuid}.sensor_measurements` |
| GET | `/api/measurements/{sensor_id}` | Sensor-specific measurements | `tenant_{uuid}.sensor_measurements` |

**Query Parameters:**
- `sensor_id` (UUID): Filter by sensor
- `equipment_id` (string): Filter by equipment
- `limit` (int): Max results (1-1000, default 100)

**Schema Routing:** All queries automatically filtered to authenticated user's tenant schema.

### Aggregation Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/aggregations/{window}` | Get aggregated statistics | `tenant_{uuid}.sensor_aggregations_{window}` |
| GET | `/api/aggregations/{window}/latest` | Latest aggregation per sensor | `tenant_{uuid}.sensor_aggregations_{window}` |
| GET | `/api/aggregations/combined/{sensor_id}` | All timeframes for sensor | All aggregation tables |

**Valid Windows:**
- `1min`: 1-minute aggregations
- `5min`: 5-minute aggregations
- `1hour`: 1-hour aggregations

**Response Fields:**
- `time`: Timestamp
- `sensor_id`: Sensor UUID
- `avg_value`: Average
- `min_value`: Minimum
- `max_value`: Maximum
- `count_values`: Sample count

### Equipment Management Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/equipment` | List all equipment | `tenant_{uuid}.equipment` |
| GET | `/api/equipment/{equipment_id}` | Get specific equipment | `tenant_{uuid}.equipment` |
| POST | `/api/equipment` | Create equipment | `tenant_{uuid}.equipment` |
| PUT | `/api/equipment/{equipment_id}` | Update equipment | `tenant_{uuid}.equipment` |
| DELETE | `/api/equipment/{equipment_id}` | Delete equipment | `tenant_{uuid}.equipment` |
| GET | `/api/equipment/{equipment_id}/sensors` | Get equipment sensors | `tenant_{uuid}.sensors` |
| POST | `/api/equipment/{equipment_id}/sensors` | Add sensor | `tenant_{uuid}.sensors` |
| DELETE | `/api/equipment/{equipment_id}/sensors/{sensor_id}` | Remove sensor | `tenant_{uuid}.sensors` |
| POST | `/api/equipment/{equipment_id}/sensors/bulk` | Bulk add sensors | `tenant_{uuid}.sensors` |

**Equipment Schema Changes:**
- **Removed:** `equipment_sensors` junction table
- **New:** Direct `sensors.equipment_id` foreign key
- **Query Pattern:** `SELECT * FROM sensors WHERE equipment_id = ?`

**Equipment Fields:**
```json
{
  "equipment_id": "pump-001",
  "equipment_type": "centrifugal_pump",
  "name": "Main Production Pump",
  "sensor_count": 52,
  "expected_sensors": ["sensor-001", "sensor-002", ...],
  "batch_timeout_seconds": 5,
  "require_complete_batch": true
}
```

### Alert Management Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/alerts` | List alerts | `tenant_{uuid}.alerts` |
| GET | `/api/alerts/unacknowledged` | Unacknowledged alerts | `tenant_{uuid}.alerts` |
| GET | `/api/alerts/critical` | Critical severity alerts | `tenant_{uuid}.alerts` |
| PATCH | `/api/alerts/{alert_id}/acknowledge` | Acknowledge alert | `tenant_{uuid}.alerts` |

**Query Parameters:**
- `sensor_id` (string): Filter by sensor
- `equipment_id` (string): Filter by equipment
- `severity` (enum): info, low, medium, high, critical
- `acknowledged` (bool): Filter by acknowledgment status
- `limit` (int): Max results (default 100, max 1000)

### Alert Rules Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/alert-rules` | List alert rules | `tenant_{uuid}.alert_rules` |
| GET | `/api/alert-rules/{rule_id}` | Get specific rule | `tenant_{uuid}.alert_rules` |
| POST | `/api/alert-rules` | Create rule | `tenant_{uuid}.alert_rules` |
| PUT | `/api/alert-rules/{rule_id}` | Update rule | `tenant_{uuid}.alert_rules` |
| DELETE | `/api/alert-rules/{rule_id}` | Delete rule | `tenant_{uuid}.alert_rules` |
| PATCH | `/api/alert-rules/{rule_id}/detection-mode` | Switch detection mode | `tenant_{uuid}.alert_rules` |

**Detection Types:**
- `threshold`: Simple threshold comparison
- `ml_anomaly`: ML model-based detection
- `statistical`: Statistical anomaly detection

### ML Models Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/ml-models` | List ML models | `tenant_{uuid}.ml_models` |
| GET | `/api/ml-models/{model_id}` | Get specific model | `tenant_{uuid}.ml_models` |
| POST | `/api/ml-models` | Create model entry | `tenant_{uuid}.ml_models` |
| PUT | `/api/ml-models/{model_id}` | Update model | `tenant_{uuid}.ml_models` |
| DELETE | `/api/ml-models/{model_id}` | Delete model | `tenant_{uuid}.ml_models` |
| POST | `/api/ml-models/train` | Initiate training | None (placeholder) |

### Training Data Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/training-data/equipment/{equipment_id}` | JSON training data | `tenant_{uuid}.sensor_measurements`, `tenant_{uuid}.sensors` |
| GET | `/api/training-data/equipment/{equipment_id}/stream` | CSV streaming | `tenant_{uuid}.sensor_measurements`, `tenant_{uuid}.sensors` |

**Query Parameters:**
- `lookback_days` (int): Historical days (1-365, default 30)
- `min_quality` (float): Quality threshold (0.0-1.0, default 0.8)
- `limit` (int): Max rows for JSON (1-100000, default 10000)

**Key Change:**
- **Old Query:** JOIN with `equipment_sensors` junction table
- **New Query:** Direct JOIN on `sensors.equipment_id`

### Company Management Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/companies` | List companies | `public.companies` |
| GET | `/api/companies/{company_id}` | Get company | `public.companies` |
| POST | `/api/companies` | Create company (admin) | `public.companies` |
| PUT | `/api/companies/{company_id}` | Update company (admin) | `public.companies` |
| DELETE | `/api/companies/{company_id}` | Delete company (admin) | `public.companies` |

**Note:** Companies table in `public` schema, accessible to all tenants via `search_path`.

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
    "sensor-001": {
      "value": 45.67,
      "timestamp": "2025-11-08T00:00:00Z",
      "equipment_id": "pump-001",
      "site_id": "factory-1",
      "company_id": "550e8400-e29b-41d4-a716-446655440000",
      "unit": "°C",
      "quality_code": 1
    }
  },
  "count": 1
}
```

### Cache Endpoints

| Method | Endpoint | Description | Schema Dependency |
|--------|----------|-------------|-------------------|
| GET | `/api/cache/sensors` | Get cached sensor values | Redis (filtered by company_id) |

**Response:**
```json
{
  "cached_sensors": 52,
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "sensors": {
    "sensor-001": { "value": 45.67, ... }
  }
}
```

### Health & System Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API root info |
| GET | `/health` | Service health check |
| GET | `/docs` | Swagger UI |
| GET | `/redoc` | ReDoc documentation |

**Health Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "timestamp": "2025-11-08T00:02:50.694557"
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
-- Resolves to: tenant_{uuid}.sensors
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
    sensor_id TEXT PRIMARY KEY,
    equipment_id TEXT REFERENCES equipment(equipment_id),
    sensor_type TEXT,
    unit TEXT,
    description TEXT,
    is_active BOOLEAN
);
```

Query pattern:
```sql
SELECT * FROM sensors WHERE equipment_id = 'pump-001';
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
  "timestamp": "2025-11-08T00:02:50.694557"
}
```

### Authentication

Register user:
```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "securepass123",
    "company_id": "550e8400-e29b-41d4-a716-446655440000",
    "role": "engineer"
  }'
```

Login:
```bash
curl -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@example.com&password=securepass123"
```

### Data Query

```bash
curl -X GET "http://localhost:8000/api/measurements?limit=10" \
  -H "Authorization: Bearer {jwt-token}"
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

Connection distribution:
```
    usename         | application_name | connections |  state
--------------------+------------------+-------------+---------
 api_gateway_user   | uvicorn          |    144      | idle
```

### Schema Routing

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
1. Token not expired (30min lifetime)
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

## References

### Internal Documentation

- IndustryFlow Database Architecture specification
- Spark Streaming Schema-Per-Tenant Architecture specification
- IndustryFlow Setup Guide

### External Resources

- FastAPI Documentation: https://fastapi.tiangolo.com/
- SQLAlchemy Async: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- fastapi-users: https://fastapi-users.github.io/fastapi-users/
- TimescaleDB: https://docs.timescale.com/
- PostgreSQL Search Path: https://www.postgresql.org/docs/current/ddl-schemas.html

### Schema-Per-Tenant Resources

- Salesforce Multi-Tenant Architecture
- AWS RDS Schema-Based Multi-Tenancy
- PostgreSQL Row-Level Security vs Schema Isolation

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

### Data Ingestion

```
POST   /api/ingest/sensor-data            # Ingest sensor data
GET    /api/ingest/stats                  # Ingestion stats
```

### Measurements

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
GET    /api/companies                     # List companies
GET    /api/companies/{company_id}        # Get company
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
