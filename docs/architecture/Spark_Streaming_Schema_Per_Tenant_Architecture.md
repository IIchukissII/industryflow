<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Spark Streaming Services: Schema-Per-Tenant Architecture

**Version:** 2.0.0
**Component:** Data Ingestion & Aggregation Layer
**Architecture:** Schema-per-tenant (v5.0)
**Date:** November 2025

---

## 1. System Architecture

### 1.1 Service Components

The streaming layer comprises two independent Spark Structured Streaming applications executing in separate containers:

| Service | Purpose | Input Source | Output Target | Trigger Interval |
|---------|---------|--------------|---------------|------------------|
| Raw Data Ingestion | Write sensor measurements | Message broker topic | TimescaleDB hypertables | 2 seconds |
| Aggregation Engine | Compute windowed statistics | Message broker topic | Aggregation tables | 5 seconds |

### 1.2 Multi-Tenant Isolation Method

**Pattern**: Schema-per-tenant  
**Isolation Level**: Database schema level  
**Rationale**: TimescaleDB hypertable compression incompatible with Row-Level Security (RLS)

**Schema Naming Convention**:
```
tenant_<normalized_tenant_id>
```

**Normalization Algorithm**:
- Input: UUID with hyphens (e.g., 550e8400-e29b-41d4-a716-446655440000)
- Process: Replace hyphens with underscores
- Output: Valid PostgreSQL identifier (e.g., tenant_550e8400_e29b_41d4_a716_446655440000)

### 1.3 Data Flow

```
Message Producer → Message Broker Topic → Spark Streaming Job → Schema Router → Tenant Schema → TimescaleDB Table
```

**Processing Steps**:
1. Message consumed from broker topic
2. JSON deserialization and schema validation
3. Tenant identifier extraction from message payload
4. Schema name computation via normalization function
5. Batch grouping by tenant identifier
6. JDBC write to fully-qualified table name (schema.table)

---

## 2. Raw Data Ingestion Service

### 2.1 Message Schema

**Input Format**: JSON messages on Kafka topic

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| timestamp | String | Yes | ISO 8601 timestamp |
| sensor_id | String (UUID) | Yes | Sensor identifier |
| equipment_id | String (UUID) | Yes | Equipment identifier |
| site_id | String (UUID) | Yes | Site identifier |
| company_id | String (UUID) | Yes | Tenant identifier |
| value | Double | Yes | Measurement value |
| unit | String | Optional | Measurement unit |
| quality_code | Integer | Optional | Data quality indicator |

**Example Message**:
```json
{
  "timestamp": "2025-11-07T21:08:00.000Z",
  "sensor_id": "a1b2c3d4-e5f6-4a5b-9c8d-7e6f5a4b3c2d",
  "equipment_id": "f1e2d3c4-b5a6-4738-9102-837465950283",
  "site_id": "d4c3b2a1-9876-5432-1098-765432109876",
  "company_id": "550e8400-e29b-41d4-a716-446655440000",
  "value": 72.5,
  "unit": "celsius",
  "quality_code": 1
}
```

### 2.2 Streaming Configuration

**Spark Configuration**:
- Execution mode: Local mode with all available cores
- Checkpoint location: Persistent volume for fault tolerance
- Processing trigger: Time-based (2 second intervals)
- Output mode: Append-only

**Kafka Consumer Configuration**:
- Bootstrap servers: Internal service DNS
- Topic subscription: Single topic (sensor-data-raw)
- Starting offset: Latest (on initial start)
- Max offsets per trigger: 10,000 messages
- Max partition fetch bytes: 1 MB

### 2.3 Processing Algorithm

```
FUNCTION write_to_timescaledb(batch_dataframe, batch_id):
    IF batch_dataframe.count() == 0:
        RETURN
    
    # Extract unique tenant identifiers from batch
    tenant_ids = batch_dataframe.select("company_id").distinct()
    
    FOR EACH tenant_id IN tenant_ids:
        IF tenant_id IS NULL:
            LOG warning and CONTINUE
        
        # Filter batch data for current tenant
        tenant_data = batch_dataframe.filter(company_id == tenant_id)
        row_count = tenant_data.count()
        
        # Compute target schema name
        schema_name = normalize_tenant_id(tenant_id)
        full_table_name = schema_name + ".sensor_measurements"
        
        TRY:
            # Execute JDBC write
            tenant_data.write.jdbc(
                url=database_connection_string,
                table=full_table_name,
                mode="append",
                properties=jdbc_properties
            )
            LOG success
        CATCH exception:
            LOG error and RAISE exception
```

### 2.4 JDBC Optimization Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| batchsize | 5000 | Group 5000 inserts per round-trip |
| reWriteBatchedInserts | true | Enable PostgreSQL multi-row INSERT optimization |
| numPartitions | 12 | Parallel JDBC connections |
| stringtype | unspecified | Allow PostgreSQL to infer UUID types |

**Performance Impact**:
- Batch writes reduce network round-trips by 5000x
- Multi-row INSERT syntax reduces parsing overhead
- Parallel partitions enable concurrent writes

### 2.5 Timestamp Transformation

**Input Format**: ISO 8601 string with optional microseconds
**Transformation**:
1. Extract first 19 characters (yyyy-MM-dd'T'HH:mm:ss)
2. Parse using timestamp format pattern
3. Store as PostgreSQL TIMESTAMPTZ

**Code Pattern**:
```python
parsed_stream = raw_stream \
    .withColumn("time", 
                to_timestamp(substring(col("timestamp"), 1, 19), 
                           "yyyy-MM-dd'T'HH:mm:ss"))
```

---

## 3. Aggregation Service

### 3.1 Windowed Aggregation Streams

Three parallel streaming queries execute simultaneously:

| Stream | Window Size | Output Table | Use Case |
|--------|-------------|--------------|----------|
| Stream 1 | 1 minute | sensor_aggregations_1min | Real-time dashboard updates |
| Stream 2 | 5 minutes | sensor_aggregations_5min | Short-term trend analysis |
| Stream 3 | 1 hour | sensor_aggregations_1hour | Long-term analytics |

### 3.2 Aggregation Functions

**Computed Metrics**:
- `avg_value`: Arithmetic mean of values in window
- `min_value`: Minimum value in window
- `max_value`: Maximum value in window
- `count_values`: Number of measurements in window

**Grouping Keys**:
- Time window (start, end)
- sensor_id
- equipment_id
- site_id
- company_id
- unit

### 3.3 Window Semantics

**Window Type**: Tumbling windows (non-overlapping)  
**Window Alignment**: End-time aligned  
**Late Data Handling**: Not configured (default: no late data processing)

**Window Example (1-minute)**:
```
Window 1: [21:00:00, 21:01:00)
Window 2: [21:01:00, 21:02:00)
Window 3: [21:02:00, 21:03:00)
```

### 3.4 Output Schema

| Field | Type | Description |
|-------|------|-------------|
| time | TIMESTAMPTZ | Window end timestamp |
| sensor_id | UUID | Sensor identifier |
| equipment_id | UUID | Equipment identifier |
| site_id | UUID | Site identifier |
| company_id | UUID | Tenant identifier |
| avg_value | DOUBLE PRECISION | Average measurement |
| min_value | DOUBLE PRECISION | Minimum measurement |
| max_value | DOUBLE PRECISION | Maximum measurement |
| count_values | INTEGER | Sample count |
| unit | VARCHAR(50) | Measurement unit |

### 3.5 Processing Algorithm

```
FUNCTION write_aggregations_to_db(batch_dataframe, batch_id, table_name):
    IF batch_dataframe.count() == 0:
        RETURN
    
    # Extract unique tenant identifiers
    tenant_ids = batch_dataframe.select("company_id").distinct()
    
    FOR EACH tenant_id IN tenant_ids:
        IF tenant_id IS NULL:
            LOG warning and CONTINUE
        
        # Filter aggregated data for tenant
        tenant_data = batch_dataframe.filter(company_id == tenant_id)
        
        # Compute fully-qualified table name
        schema_name = normalize_tenant_id(tenant_id)
        full_table_name = schema_name + "." + table_name
        
        TRY:
            # Write to tenant-specific aggregation table
            tenant_data.write.jdbc(
                url=database_connection_string,
                table=full_table_name,
                mode="append",
                properties=jdbc_properties
            )
            LOG success
        CATCH exception:
            LOG error (non-fatal)
```

---

## 4. Database Integration

### 4.1 Connection Configuration

**Connection Pool Parameters**:
- Database user: Dedicated application role (not superuser)
- Connection protocol: JDBC PostgreSQL driver
- SSL mode: Disabled (internal network)
- Connection timeout: Default
- Pool pre-ping: Enabled for connection validation

**Environment Variables Required**:
- `TIMESCALEDB_HOST`: Database service hostname
- `TIMESCALEDB_PORT`: Database port (default: 5432)
- `TIMESCALEDB_DB`: Database name
- `SPARK_STREAMING_DB_USER`: Application role username
- `SPARK_STREAMING_DB_PASSWORD`: Application role password

### 4.2 Database Role Permissions

**Role Name**: spark_streaming_user  
**Connection Limit**: 50 concurrent connections

**Required Permissions**:
```sql
-- Schema access
GRANT USAGE ON SCHEMA tenant_<uuid> TO spark_streaming_user;

-- Table permissions
GRANT INSERT, SELECT ON ALL TABLES IN SCHEMA tenant_<uuid> TO spark_streaming_user;

-- Future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA tenant_<uuid> 
  GRANT INSERT, SELECT ON TABLES TO spark_streaming_user;

-- Sequence access (for SERIAL columns)
GRANT USAGE ON ALL SEQUENCES IN SCHEMA tenant_<uuid> TO spark_streaming_user;
```

### 4.3 Target Table Structures

**Raw Measurements Table** (`sensor_measurements`):
- Primary key: Composite (time, sensor_id)
- Hypertable: Partitioned by time column
- Chunk interval: 1 day
- Compression: Enabled (90-95% savings)
- Compression policy: Compress chunks older than 7 days
- Retention policy: Drop chunks older than 90 days

**Aggregation Tables** (`sensor_aggregations_*`):
- Primary key: Composite (time, sensor_id)
- Hypertable: Partitioned by time column
- Chunk interval: 7 days (1min, 5min), 30 days (1hour)
- Compression: Enabled
- Compression policy: Compress chunks older than 14 days
- Retention policy: 
  - 1min: 30 days
  - 5min: 90 days
  - 1hour: 365 days

---

## 5. Fault Tolerance & Recovery

### 5.1 Checkpoint Mechanism

**Purpose**: Enable exactly-once processing semantics  
**Storage**: Persistent Docker volumes

**Checkpoint Contents**:
- Kafka offset tracking (per partition)
- Batch commit markers
- State store snapshots (for aggregations)
- Query metadata

**Checkpoint Locations**:
- Raw ingestion: `/opt/spark/checkpoints`
- 1-minute aggregations: `/opt/spark/checkpoints-sensor_aggregations_1min`
- 5-minute aggregations: `/opt/spark/checkpoints-sensor_aggregations_5min`
- 1-hour aggregations: `/opt/spark/checkpoints-sensor_aggregations_1hour`

### 5.2 Recovery Behavior

**On Service Restart**:
1. Read checkpoint metadata
2. Resume from last committed Kafka offset
3. Recompute partial batches if necessary
4. Continue processing from recovery point

**Data Consistency Guarantees**:
- Exactly-once semantics for database writes
- Idempotent write operations
- Transactional batch commits

### 5.3 Error Handling

**Fatal Errors** (cause service shutdown):
- Kafka broker unavailable
- Database connection failure
- Invalid checkpoint state
- JDBC write failure

**Non-Fatal Errors** (logged but processing continues):
- Malformed JSON messages (skipped)
- Null tenant identifiers (filtered)
- Individual aggregation batch failures

---

## 6. Performance Characteristics

### 6.1 Throughput Metrics

**Target Performance**:
- Raw ingestion: 300+ messages/second
- Aggregation processing: 50+ windows/second per stream
- End-to-end latency: < 5 seconds (raw data to database)

**Bottleneck Analysis**:
- Primary bottleneck: Database write throughput
- Secondary bottleneck: Kafka consumer throughput
- Mitigation: Batch writes, parallel partitions, connection pooling

### 6.2 Resource Utilization

**CPU Usage**:
- Spark master: Minimal (coordination only)
- Spark worker: 4 cores allocated
- Streaming jobs: Variable (2-4 cores during processing)

**Memory Allocation**:
- Driver memory: 4GB
- Executor memory: 4GB
- JVM heap: Auto-tuned by Spark

**Network Bandwidth**:
- Kafka ingress: ~1-5 MB/s
- Database egress: ~2-10 MB/s (depends on batch size)

### 6.3 Scaling Considerations

**Horizontal Scaling**:
- Increase Kafka topic partitions
- Add Spark worker nodes
- Scale database write capacity

**Vertical Scaling**:
- Increase executor memory for larger state stores
- Add CPU cores for parallel processing
- Tune batch sizes for network optimization

---

## 7. Monitoring & Observability

### 7.1 Log Output

**Log Levels**:
- INFO: Batch processing events, connection status
- WARN: Configuration warnings, non-fatal errors
- ERROR: Fatal errors, exceptions

**Key Log Patterns**:
```
Batch {batch_id}: Writing {row_count} rows to {schema}.{table}
✅ SUCCESS: Wrote {row_count} rows to {schema}.{table}
❌ ERROR writing to {schema}.{table}: {error_message}
```

### 7.2 Metrics Collection

**Spark UI Metrics** (port 4040):
- Input rate (records/second)
- Processing time per batch
- Total delay
- Number of active queries

**Application Metrics**:
- Messages processed per tenant
- Write latency per schema
- Checkpoint duration
- State store size

### 7.3 Health Indicators

**Healthy State**:
- No exceptions in logs
- Batch processing time < trigger interval
- Checkpoint writes succeeding
- Stable state store size

**Degraded State**:
- Processing time approaching trigger interval
- Increasing checkpoint duration
- Growing state store size

**Failure State**:
- Service container exited
- Repeated JDBC write failures
- Kafka consumer lag increasing unbounded

---

## 8. Deployment Configuration

### 8.1 Container Orchestration

**Service Definitions** (Docker Compose):

```yaml
spark-streaming:
  image: custom/spark-streaming:latest
  container_name: spark-streaming
  environment:
    SPARK_MASTER: local[*]
    CHECKPOINT_LOCATION: /opt/spark/checkpoints
    KAFKA_BOOTSTRAP_SERVERS: kafka:29092
    TIMESCALEDB_HOST: timescaledb
    TIMESCALEDB_PORT: 5432
    TIMESCALEDB_DB: industryflow
    SPARK_STREAMING_DB_USER: spark_streaming_user
    SPARK_STREAMING_DB_PASSWORD: ${SPARK_STREAMING_DB_PASSWORD}
  volumes:
    - spark-checkpoints-streaming:/opt/spark/checkpoints
  depends_on:
    - kafka
    - timescaledb
  restart: unless-stopped

spark-aggregations:
  image: custom/spark-aggregations:latest
  container_name: spark-aggregations
  environment:
    # Same as spark-streaming
  volumes:
    - spark-checkpoints-aggregations:/opt/spark/checkpoints
  depends_on:
    - kafka
    - timescaledb
  restart: unless-stopped
```

### 8.2 Container Build

**Base Image**: apache/spark:3.5.0  
**Additional Dependencies**:
- Python 3 runtime
- psycopg2-binary (PostgreSQL adapter)
- Kafka connector JAR
- PostgreSQL JDBC driver

**Build Process**:
1. Install system dependencies
2. Install Python packages
3. Copy application code
4. Pre-download Spark packages (optimization)
5. Set proper file permissions
6. Define entry point

### 8.3 Network Configuration

**Service Communication**:
- Kafka: Internal DNS (kafka:29092)
- TimescaleDB: Internal DNS (timescaledb:5432)
- Network mode: Bridge network

**Port Exposure**:
- Spark UI: 4040 (diagnostic only, not exposed)
- No external ports required for operation

---

## 9. Testing & Validation

### 9.1 Unit Testing

**Test Coverage**:
- Schema normalization function
- Batch processing logic
- Error handling paths
- JDBC parameter configuration

### 9.2 Integration Testing

**Test Scenarios**:
1. Write messages to Kafka topic
2. Verify data appears in correct tenant schema
3. Validate aggregation computations
4. Test multi-tenant data isolation

**Validation Queries**:
```sql
-- Verify raw data ingestion
SELECT COUNT(*) FROM tenant_<uuid>.sensor_measurements
WHERE time > NOW() - INTERVAL '5 minutes';

-- Verify aggregations
SELECT COUNT(*) FROM tenant_<uuid>.sensor_aggregations_1min
WHERE time > NOW() - INTERVAL '10 minutes';

-- Verify tenant isolation
SELECT company_id, COUNT(*) FROM tenant_<uuid>.sensor_measurements
GROUP BY company_id;
```

### 9.3 Performance Testing

**Load Test Parameters**:
- Message rate: 100-1000 messages/second
- Test duration: 60 minutes
- Number of tenants: 3-10
- Sensors per tenant: 50-500

**Success Criteria**:
- Processing lag < 10 seconds
- No message loss
- CPU utilization < 80%
- Memory stable (no leaks)

---

## 10. Operational Procedures

### 10.1 Service Startup

**Prerequisite Checks**:
1. Kafka topic exists (sensor-data-raw)
2. Database schemas created for all tenants
3. Application role permissions granted
4. Checkpoint volumes initialized

**Startup Sequence**:
1. Start Kafka and Zookeeper
2. Start TimescaleDB
3. Start Spark streaming service
4. Start Spark aggregations service
5. Verify log output shows successful connection

### 10.2 Adding New Tenant

**Required Steps**:
1. Create tenant schema in database
2. Create all required tables (sensor_measurements, aggregations)
3. Grant permissions to spark_streaming_user
4. No service restart required (dynamic routing)

**Verification**:
```sql
-- Check schema exists
SELECT schema_name FROM information_schema.schemata 
WHERE schema_name = 'tenant_<uuid>';

-- Check permissions
SELECT has_schema_privilege('spark_streaming_user', 'tenant_<uuid>', 'USAGE');
```

### 10.3 Service Shutdown

**Graceful Shutdown**:
1. Send SIGTERM to container
2. Spark completes current batch
3. Writes final checkpoint
4. Closes connections
5. Container exits

**Checkpoint Preservation**:
- Checkpoint volumes persist across container restarts
- Data recovery possible on next startup

---

## 11. Troubleshooting Guide

### 11.1 Common Issues

**Issue**: Service fails to start with "Unknown topic" error  
**Cause**: Kafka topic not created  
**Solution**: Create topic using Kafka admin tools

**Issue**: "Permission denied for schema" error  
**Cause**: Insufficient database permissions  
**Solution**: Grant USAGE and table permissions to application role

**Issue**: Processing lag increasing  
**Cause**: Write throughput lower than ingestion rate  
**Solution**: Increase batch size, add database connections, or partition data

**Issue**: "Null company_id" warnings in logs  
**Cause**: Malformed messages missing tenant identifier  
**Solution**: Fix message producer to include company_id field

### 11.2 Diagnostic Commands

**Check service status**:
```bash
docker ps | grep spark
```

**View service logs**:
```bash
docker logs spark-streaming --tail 100
docker logs spark-aggregations --tail 100
```

**Check Kafka consumer group status**:
```bash
kafka-consumer-groups --bootstrap-server localhost:9092 \
  --group spark-streaming --describe
```

**Verify database writes**:
```sql
SELECT MAX(time) as latest_write FROM tenant_<uuid>.sensor_measurements;
```

### 11.3 Performance Tuning

**Increase throughput**:
- Increase `maxOffsetsPerTrigger` (Kafka)
- Increase `batchsize` (JDBC)
- Decrease `processingTime` trigger interval

**Reduce latency**:
- Decrease trigger interval
- Reduce batch size
- Increase parallelism (`numPartitions`)

**Optimize memory**:
- Tune `spark.executor.memory`
- Adjust `spark.memory.fraction`
- Monitor state store size

---

## 12. Migration from Row-Level Security

### 12.1 Architectural Changes

**Previous (RLS-based)**:
- Single schema for all tenants
- Row-Level Security policies filter by company_id
- `SET app.current_company_id` before queries

**Current (Schema-per-tenant)**:
- Dedicated schema per tenant
- No RLS policies required
- Dynamic schema routing in application code

### 12.2 Code Migration Pattern

**Old Pattern**:
```python
session.execute(f"SET app.current_company_id = '{company_id}'")
session.query(SensorMeasurements).filter_by(equipment_id=eq_id)
```

**New Pattern**:
```python
schema_name = normalize_tenant_id(company_id)
table_name = f"{schema_name}.sensor_measurements"
dataframe.write.jdbc(url=jdbc_url, table=table_name, ...)
```

### 12.3 Benefits of Schema-Per-Tenant

**Advantages**:
- Compatible with TimescaleDB compression (90-95% storage savings)
- Improved query performance (no RLS overhead)
- Better tenant isolation and security
- Simplified backup and restore (per-schema)
- Standard pattern used by major SaaS platforms

**Trade-offs**:
- More complex application routing logic
- Schema proliferation (one schema per tenant)
- Migration effort for existing systems

---

## 13. Security Considerations

### 13.1 Authentication

**Database Authentication**:
- Dedicated application role (not superuser)
- Password stored in environment variables
- No password in code or logs

**Message Broker Authentication**:
- PLAINTEXT protocol (internal network)
- No SASL authentication required
- Network isolation via Docker bridge

### 13.2 Authorization

**Principle of Least Privilege**:
- Application role has INSERT and SELECT only
- No DELETE, UPDATE, or DDL permissions
- Schema-level access control

**Permission Verification**:
```sql
-- Verify limited permissions
SELECT privilege_type FROM information_schema.role_table_grants
WHERE grantee = 'spark_streaming_user' AND table_name = 'sensor_measurements';
```

### 13.3 Data Isolation

**Tenant Isolation Mechanisms**:
- Physical separation via schemas
- No shared tables between tenants
- Application-enforced routing logic

**Isolation Verification**:
```sql
-- Verify no cross-tenant data leakage
SELECT COUNT(*) FROM tenant_<uuid_A>.sensor_measurements AS a
JOIN tenant_<uuid_B>.sensor_measurements AS b ON a.sensor_id = b.sensor_id;
-- Expected: 0 rows
```

---

## 14. Future Enhancements

### 14.1 Potential Improvements

**Performance Optimization**:
- Implement Delta Lake for ACID guarantees
- Add Apache Kafka exactly-once semantics
- Implement dynamic batch sizing based on load

**Operational Features**:
- Add Prometheus metrics exporter
- Implement custom healthcheck endpoints
- Add Grafana dashboards for monitoring

**Data Quality**:
- Schema validation before write
- Data quality checks (range validation, outlier detection)
- Dead letter queue for malformed messages

### 14.2 Scalability Roadmap

**Phase 1** (Current):
- Single Spark executor per service
- Local mode processing

**Phase 2** (Future):
- Spark cluster mode with multiple workers
- Distributed processing across nodes

**Phase 3** (Future):
- Kubernetes deployment
- Auto-scaling based on load
- Multi-region deployment

---

## 15. References

### 15.1 Technology Stack

- **Apache Spark**: 3.5.0 (Structured Streaming)
- **Kafka**: 7.5.0 (Confluent Platform)
- **TimescaleDB**: Latest (PostgreSQL 15)
- **Python**: 3.x runtime
- **JDBC Driver**: PostgreSQL 42.6.0

### 15.2 Design Patterns

- **Multi-tenancy**: Schema-per-tenant pattern
- **Stream Processing**: Micro-batch with checkpointing
- **Data Partitioning**: Time-based hypertables
- **Fault Tolerance**: Checkpoint-based recovery

### 15.3 Related Documentation

- Database schema architecture document
- API gateway tenant routing documentation
- MLOps pipeline integration guide
- Deployment and operations manual

---

**Document Version**: 1.0  
**Last Updated**: November 2025  
