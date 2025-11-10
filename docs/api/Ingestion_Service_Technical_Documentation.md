# Ingestion Service - Technical Documentation

**Service:** Ingestion Service  
**Version:** 1.0.0  
**Port:** 8003  
**Protocol:** HTTP/REST  
**Authentication:** JWT Bearer Token

---

## 1. Service Overview

### 1.1 Purpose

High-throughput sensor data ingestion endpoint for IoT devices. Separates data ingestion traffic from REST API operations to prevent performance bottlenecks in the API Gateway.

### 1.2 Responsibilities

- Accept authenticated sensor data submissions
- Validate JWT tokens and resolve company_id
- Publish sensor data to Kafka message queue
- Enforce multi-tenant data isolation

### 1.3 Architecture Position

```
IoT Devices/Clients → Ingestion Service (8003) → Kafka → Spark Streaming
                              ↓
                        TimescaleDB (auth lookup)
```

---

## 2. API Endpoints

### 2.1 Data Ingestion Endpoint

**Endpoint:** `POST /ingest`  
**Authentication:** Required (JWT Bearer Token)  
**Content-Type:** `application/json`  
**Response Status:** `202 Accepted` (asynchronous processing)

#### Request Headers

| Header | Type | Required | Description |
|--------|------|----------|-------------|
| Authorization | string | Yes | Bearer token obtained from authentication endpoint |
| Content-Type | string | Yes | Must be `application/json` |

#### Request Body Schema

| Field | Type | Required | Format | Description |
|-------|------|----------|--------|-------------|
| timestamp | string | Yes | ISO 8601 | Data collection timestamp (UTC) |
| sensor_id | string | Yes | UUID | Sensor identifier |
| equipment_id | string | Yes | UUID | Parent equipment identifier |
| site_id | string | Yes | string | Installation site identifier |
| value | number | Yes | float | Sensor measurement value |
| unit | string | Yes | string | Measurement unit (e.g., "celsius", "bar") |
| quality_code | integer | No | int | Data quality indicator (0=good, default: 0) |

#### Request Example

```json
{
  "timestamp": "2025-11-10T19:30:00Z",
  "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
  "equipment_id": "550e8400-e29b-41d4-a716-446655440010",
  "site_id": "factory_north",
  "value": 75.5,
  "unit": "celsius",
  "quality_code": 0
}
```

#### Response Schema

| Field | Type | Description |
|-------|------|-------------|
| status | string | Processing status ("accepted") |
| message | string | Confirmation message |
| company_id | string | Resolved company identifier (UUID) |
| sensor_id | string | Echo of submitted sensor_id |
| timestamp | string | Echo of submitted timestamp |

#### Response Example

```json
{
  "status": "accepted",
  "message": "Sensor data queued for processing",
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "sensor_id": "550e8400-e29b-41d4-a716-446655440020",
  "timestamp": "2025-11-10T19:30:00Z"
}
```

#### Error Responses

| Status Code | Error | Description |
|-------------|-------|-------------|
| 401 | Invalid token | JWT token verification failed |
| 401 | Token expired | JWT token has expired |
| 404 | User not found | Authenticated user not found in any tenant |
| 422 | Validation Error | Request body validation failed |
| 503 | Service Unavailable | Kafka producer unavailable |

---

## 3. Authentication Flow

### 3.1 Token Acquisition

Obtain JWT token from API Gateway authentication endpoint:

```
POST http://localhost:8000/auth/jwt/login
Content-Type: application/x-www-form-urlencoded

username=user@company.com&password=password
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

### 3.2 Token Usage

Include token in Authorization header:

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### 3.3 Company ID Resolution Algorithm

```
1. Extract user_id from JWT token (sub claim)
2. Query all tenant schemas in TimescaleDB
3. For each tenant schema:
   a. Set search_path to tenant schema
   b. Query: SELECT company_id FROM "user" WHERE id = user_id
   c. If found, return company_id
4. If not found in any tenant, return 404 error
```

Complexity: O(n) where n = number of tenant schemas

---

## 4. Data Processing Pipeline

### 4.1 Ingestion Flow

```
1. Receive HTTP POST request
2. Validate JWT token (jose library)
3. Resolve company_id from database
4. Construct Kafka message with company_id
5. Publish to Kafka topic: sensor-data-raw
6. Return 202 Accepted response
```

### 4.2 Kafka Message Format

Published message structure:

| Field | Source | Description |
|-------|--------|-------------|
| timestamp | Request body | ISO 8601 timestamp |
| sensor_id | Request body | Sensor UUID |
| equipment_id | Request body | Equipment UUID |
| site_id | Request body | Site identifier string |
| company_id | JWT authentication | Resolved company UUID (prevents spoofing) |
| value | Request body | Measurement value |
| unit | Request body | Measurement unit |
| quality_code | Request body | Quality indicator (default: 0) |

**Security Note:** `company_id` is **enforced** from JWT authentication, not accepted from request body. This prevents cross-tenant data injection attacks.

### 4.3 Kafka Configuration

| Parameter | Value | Description |
|-----------|-------|-------------|
| Bootstrap Servers | kafka:29092 | Kafka broker address |
| Topic | sensor-data-raw | Target topic for sensor data |
| Producer Type | Asynchronous | Non-blocking message publishing |
| Acks | 1 | Wait for leader acknowledgment |

---

## 5. Multi-Tenant Isolation

### 5.1 Schema-per-Tenant Architecture

Each company has dedicated PostgreSQL schema:

```
tenant_550e8400_e29b_41d4_a716_446655440000  (Company A)
tenant_550e8400_e29b_41d4_a716_446655440001  (Company B)
tenant_550e8400_e29b_41d4_a716_446655440002  (Company C)
```

### 5.2 Isolation Mechanism

- JWT token contains user_id only (no company_id)
- Ingestion service queries database to resolve company_id
- Company ID injected into Kafka message
- Downstream services route data to correct tenant schema

### 5.3 Security Properties

1. **Authentication Required:** All requests must include valid JWT token
2. **Company ID Enforcement:** Company ID resolved from user authentication, not request body
3. **Schema Isolation:** User lookup queries only accessible tenant schemas
4. **Authorization:** Users can only submit data for their own company

---

## 6. Configuration

### 6.1 Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| INGESTION_SERVICE_PORT | Yes | - | Service listen port |
| DB_HOST | Yes | - | TimescaleDB hostname |
| DB_PORT | Yes | - | TimescaleDB port |
| DB_NAME | Yes | - | Database name (industryflow) |
| INGESTION_SERVICE_DB_USER | Yes | - | Database username |
| INGESTION_SERVICE_DB_PASSWORD | Yes | - | Database password |
| KAFKA_BOOTSTRAP_SERVERS | Yes | - | Kafka broker address |
| KAFKA_TOPIC_RAW | No | sensor-data-raw | Target Kafka topic |
| JWT_SECRET_KEY | Yes | - | JWT signing secret (must match API Gateway) |
| JWT_ALGORITHM | Yes | - | JWT algorithm (HS256) |
| CORS_ORIGINS | No | * | CORS allowed origins |

**Critical:** `JWT_SECRET_KEY` must be identical to API Gateway configuration. Mismatch will cause authentication failures.

### 6.2 Database Connection Pool

| Parameter | Value | Description |
|-----------|-------|-------------|
| Min Size | 5 | Minimum persistent connections |
| Max Size | 20 | Maximum concurrent connections |
| Database | industryflow | Target database |
| User | ingestion_service_user | Database role |

### 6.3 Required Database Permissions

```sql
-- Schema access
GRANT USAGE ON SCHEMA tenant_* TO ingestion_service_user;

-- User table access (for company_id resolution)
GRANT SELECT ON public."user" TO ingestion_service_user;

-- Information schema (for tenant discovery)
GRANT SELECT ON information_schema.schemata TO ingestion_service_user;
```

---

## 7. Performance Characteristics

### 7.1 Expected Throughput

- Single instance: 1,000+ requests/second
- Async Kafka publishing: Non-blocking I/O
- Database queries: Cached connection pool

### 7.2 Latency Profile

| Operation | Typical Latency |
|-----------|-----------------|
| JWT validation | 1-3ms |
| Company ID resolution | 5-10ms (O(n) with tenant count) |
| Kafka publish | 2-5ms (async) |
| Total response time | 10-20ms |

### 7.3 Scaling Considerations

- **Horizontal Scaling:** Deploy multiple instances behind load balancer
- **Connection Pool:** Adjust pool size based on concurrent load
- **Company ID Caching:** Consider Redis cache for user→company_id mapping
- **Kafka Partitioning:** Use company_id as partition key for data locality

---

## 8. Error Handling

### 8.1 Authentication Errors

| Error | HTTP Status | Response |
|-------|-------------|----------|
| Missing token | 403 | `{"detail": "Not authenticated"}` |
| Invalid signature | 401 | `{"detail": "Invalid token: Signature verification failed"}` |
| Expired token | 401 | `{"detail": "Invalid token: ..."}` |
| User not found | 404 | `{"detail": "User not found in any tenant"}` |

### 8.2 Validation Errors

```json
{
  "detail": [
    {
      "loc": ["body", "timestamp"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### 8.3 Service Errors

| Condition | HTTP Status | Response |
|-----------|-------------|----------|
| Kafka unavailable | 503 | `{"detail": "Failed to queue message to Kafka"}` |
| Database unavailable | 503 | `{"detail": "Database pool not initialized"}` |
| Internal error | 500 | `{"detail": "Internal server error: ..."}` |

---

## 9. Integration Points

### 9.1 Upstream Dependencies

| Service | Purpose | Endpoint |
|---------|---------|----------|
| API Gateway | JWT token issuance | http://api-gateway:8000/auth/jwt/login |
| TimescaleDB | Company ID resolution | timescaledb:5432/industryflow |

### 9.2 Downstream Consumers

| Service | Purpose | Interface |
|---------|---------|-----------|
| Kafka | Message queue | kafka:29092 (topic: sensor-data-raw) |
| Spark Streaming | Real-time processing | Kafka consumer |
| Alert Detection | Anomaly detection | Kafka consumer |

### 9.3 Data Flow Diagram

```
Client Application
       |
       | POST /ingest + JWT
       ↓
Ingestion Service (8003)
       |
       |-- (auth) → TimescaleDB (company_id lookup)
       |
       |-- (publish) → Kafka (sensor-data-raw topic)
       |
       ↓
[Spark Streaming, Alert Service] ← consume from Kafka
       |
       ↓
TimescaleDB (tenant schemas)
```

---

## 10. Deployment

### 10.1 Docker Container

```yaml
ingestion-service:
  image: industryflow-v2-ingestion-service
  ports:
    - "8003:8003"
  environment:
    - DB_HOST=timescaledb
    - KAFKA_BOOTSTRAP_SERVERS=kafka:29092
    - JWT_SECRET_KEY=${JWT_SECRET_KEY}
  depends_on:
    - timescaledb
    - kafka
```

### 10.2 Health Check

**Endpoint:** `GET /health`  
**Response:**
```json
{
  "status": "healthy",
  "service": "ingestion",
  "port": 8003
}
```

### 10.3 Startup Sequence

```
1. Load configuration from environment variables
2. Initialize database connection pool
3. Initialize Kafka producer
4. Start FastAPI application on port 8003
5. Ready to accept requests
```

### 10.4 Shutdown Sequence

```
1. Stop accepting new requests
2. Wait for in-flight requests to complete
3. Close Kafka producer
4. Close database connection pool
5. Exit
```

---

## 11. Monitoring and Logging

### 11.1 Log Levels

| Level | Usage |
|-------|-------|
| INFO | Startup events, successful operations |
| ERROR | Authentication failures, Kafka errors, database errors |
| DEBUG | JWT validation details (disabled in production) |

### 11.2 Key Log Events

```
INFO - 🚀 Ingestion Service starting...
INFO - ✅ Database pool created
INFO - ✅ Kafka producer initialized
INFO - ✅ Ingestion Service started successfully
INFO - 172.18.0.1:50568 - "POST /ingest HTTP/1.1" 202 Accepted
```

### 11.3 Metrics to Monitor

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| Request rate | Requests per second | - |
| Response time p95 | 95th percentile latency | > 50ms |
| Error rate | Failed requests / total | > 1% |
| Kafka publish failures | Failed Kafka publishes | > 0.1% |
| Database connection pool | Active connections | > 18/20 |
| Authentication failures | Invalid token attempts | > 5% |

---

## 12. Security Considerations

### 12.1 Authentication Security

- JWT tokens signed with HS256 algorithm
- Tokens validated on every request
- Expired tokens rejected automatically
- Secret key must be cryptographically random (88+ characters)

### 12.2 Data Security

- Company ID enforced from authentication (prevents spoofing)
- No direct database writes (data flows through Kafka)
- Schema-per-tenant isolation at database level
- TLS recommended for production deployments

### 12.3 Attack Mitigation

| Attack Vector | Mitigation |
|---------------|------------|
| Token replay | Token expiration (configurable TTL) |
| Cross-tenant injection | Company ID from JWT, not request body |
| SQL injection | Parameterized queries, no string concatenation |
| DDoS | Rate limiting (implement at load balancer) |

---

## 13. Troubleshooting

### 13.1 Common Issues

**Issue:** "Invalid token: Signature verification failed"  
**Cause:** JWT_SECRET_KEY mismatch between API Gateway and Ingestion Service  
**Solution:** Verify environment variable values match exactly

**Issue:** "User not found in any tenant"  
**Cause:** User exists in API Gateway auth tables but not in tenant schemas  
**Solution:** Ensure user record exists in appropriate tenant schema

**Issue:** "Failed to queue message to Kafka"  
**Cause:** Kafka broker unavailable or topic does not exist  
**Solution:** Verify Kafka connectivity and topic creation

**Issue:** High latency (>50ms)  
**Cause:** Company ID resolution scanning many tenant schemas  
**Solution:** Implement Redis cache for user→company_id mapping

### 13.2 Diagnostic Commands

```bash
# Check service logs
docker logs industryflow-ingestion-service --tail 50

# Test authentication
TOKEN=$(curl -s -X POST http://localhost:8000/auth/jwt/login \
  -d "username=user@company.com&password=pass" | jq -r '.access_token')

# Test ingestion
curl -X POST http://localhost:8003/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"timestamp":"2025-11-10T20:00:00Z",...}'

# Verify database connectivity
docker exec ingestion-service psql -U ingestion_service_user -d industryflow -c "SELECT 1"

# Check Kafka connectivity
docker exec ingestion-service nc -zv kafka 29092
```

---

## 14. API Specification Summary

| Aspect | Value |
|--------|-------|
| Base URL | http://localhost:8003 |
| Protocol | HTTP/1.1 |
| Authentication | JWT Bearer Token |
| Request Format | JSON |
| Response Format | JSON |
| Async Processing | Yes (Kafka queue) |
| Max Request Size | 1MB (configurable) |
| Timeout | 30 seconds |
| Rate Limiting | Not implemented (add at load balancer) |

---

**Document Version:** 1.0  
**Last Updated:** November 10, 2025  
**Service Version:** 1.0.0
