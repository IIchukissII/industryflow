# Alert Detection Service - Technical Documentation
## Schema-per-Tenant Architecture

**Version:** 2.0  
**Architecture Pattern:** Schema-per-Tenant Multi-Tenancy  
**Processing Model:** Event-Driven Real-Time Detection

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Design](#2-architecture-design)
3. [Multi-Tenant Isolation](#3-multi-tenant-isolation)
4. [Rule Evaluation Engine](#4-rule-evaluation-engine)
5. [Message Processing Pipeline](#5-message-processing-pipeline)
6. [Database Schema](#6-database-schema)
7. [Component Interfaces](#7-component-interfaces)
8. [Algorithms](#8-algorithms)
9. [Performance Characteristics](#9-performance-characteristics)
10. [Deployment Specifications](#10-deployment-specifications)

---

## 1. System Overview

### 1.1 Purpose

Real-time alert detection service that evaluates sensor data streams against configurable rules and generates alerts when threshold violations or anomalies are detected. Supports multiple tenant organizations with complete data isolation.

### 1.2 Core Capabilities

- **Real-time Processing:** Event-driven evaluation of sensor data streams
- **Multi-Tenant Isolation:** Schema-per-tenant database architecture
- **Rule Types:** Threshold-based and ML-based detection
- **Dynamic Rules:** Runtime rule reloading without service restart
- **Horizontal Scalability:** Stateless design with consumer group partitioning

### 1.3 System Boundaries

**Inputs:**
- Sensor data messages from Kafka topic
- Alert rule configurations from database

**Outputs:**
- Alert records to database (persistent storage)
- Alert notifications to Kafka topic (downstream consumption)

**External Dependencies:**
- TimescaleDB (rule storage, alert persistence)
- Apache Kafka (message streaming)
- ML model files (for ML-based rules)

---

## 2. Architecture Design

### 2.1 Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Alert Detection Service                     │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Service    │    │  Repository  │    │    Rules     │
│   Manager    │───▶│    Layer     │───▶│    Engine    │
└──────┬───────┘    └──────────────┘    └──────┬───────┘
       │                                         │
       ▼                                         │
┌──────────────┐                                │
│    Kafka     │                                │
│   Consumer   │────────────────────────────────┘
└──────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│                    Message Queue (Kafka)                  │
└──────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│           Database (Schema-per-Tenant)                    │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│  │ Tenant A   │  │ Tenant B   │  │ Tenant C   │        │
│  │ Schema     │  │ Schema     │  │ Schema     │        │
│  └────────────┘  └────────────┘  └────────────┘        │
└──────────────────────────────────────────────────────────┘
```

### 2.2 Component Responsibilities

**Service Manager:**
- Lifecycle management (initialization, shutdown)
- Periodic rule reloading
- Database connection pool management
- Background task coordination

**Repository Layer:**
- Tenant schema discovery
- Rule loading from all tenant schemas
- Alert persistence with schema routing
- Transaction management

**Rules Engine:**
- Rule organization by tenant
- Sensor-to-rule matching
- Threshold evaluation
- ML model invocation
- Alert generation

**Kafka Consumer:**
- Message consumption from sensor data topic
- Deserialization and validation
- Company-based routing to rules engine
- Alert publishing to output topic

### 2.3 Design Patterns

**Pattern: Repository Pattern**
- Abstracts database operations
- Centralizes schema routing logic
- Provides clean separation of data access

**Pattern: Strategy Pattern**
- Detection type abstraction (threshold, ML)
- Pluggable evaluation algorithms
- Runtime detection mode switching

**Pattern: Event-Driven Architecture**
- Kafka-based message processing
- Asynchronous alert generation
- Decoupled from data ingestion

---

## 3. Multi-Tenant Isolation

### 3.1 Schema-per-Tenant Model

**Principle:** Each tenant's data resides in a dedicated database schema with identical table structures.

**Schema Naming Convention:**
```
tenant_<normalized_uuid>
```

**UUID Normalization Algorithm:**
```
Input:  550e8400-e29b-41d4-a716-446655440000
Step 1: Remove hyphens → 550e8400e29b41d4a716446655440000
Step 2: Replace with underscores → 550e8400_e29b_41d4_a716_446655440000
Output: tenant_550e8400_e29b_41d4_a716_446655440000
```

### 3.2 Tenant Routing Mechanism

**Discovery Phase (Startup):**
```sql
-- Enumerate all tenant schemas
SELECT schema_name 
FROM information_schema.schemata 
WHERE schema_name LIKE 'tenant_%'
ORDER BY schema_name;
```

**Per-Request Routing:**
```sql
-- Extract company_id from message
company_id = message['company_id']

-- Normalize to schema name
schema_name = normalize_company_id_to_schema(company_id)

-- Set search path for session
SET search_path TO <schema_name>, public;

-- Execute query (automatic routing)
SELECT * FROM alert_rules WHERE enabled = true;
```

### 3.3 Isolation Guarantees

**Database Level:**
- PostgreSQL search_path provides namespace isolation
- No cross-schema queries without explicit qualification
- Schema-level permissions prevent unauthorized access

**Application Level:**
- Company ID extracted from every message
- Rules organized by tenant in memory
- Evaluation scoped to message's tenant

**Benefits:**
- No company_id columns in tenant tables
- Simplified query logic
- Compatible with TimescaleDB compression
- Clear audit trail via search_path logging

---

## 4. Rule Evaluation Engine

### 4.1 Rule Organization

**In-Memory Structure:**
```python
tenant_rules = {
    'company_uuid_1': [rule_1, rule_2, rule_3],
    'company_uuid_2': [rule_4, rule_5],
    'company_uuid_3': [rule_6]
}
```

**Rule Attributes:**
- `rule_id`: Unique identifier (UUID)
- `name`: Human-readable rule name
- `sensor_id`: Target sensor (UUID, optional)
- `equipment_id`: Target equipment (UUID, optional)
- `detection_type`: 'threshold' | 'ml' | 'statistical'
- `threshold_min`: Lower bound (optional)
- `threshold_max`: Upper bound (optional)
- `model_id`: ML model reference (UUID, optional)
- `severity`: 'low' | 'medium' | 'high' | 'critical'
- `enabled`: Active status (boolean)

### 4.2 Evaluation Algorithm

```
FUNCTION evaluate(sensor_data, company_id):
    // Input validation
    IF sensor_data.sensor_id is NULL OR sensor_data.value is NULL:
        RETURN empty_list
    
    // Get tenant rules
    rules = tenant_rules[company_id]
    IF rules is EMPTY:
        RETURN empty_list
    
    // Filter applicable rules
    applicable = filter_applicable_rules(
        sensor_data.sensor_id,
        sensor_data.equipment_id,
        rules
    )
    
    // Evaluate each rule
    triggered_alerts = []
    FOR EACH rule IN applicable:
        alert = evaluate_rule(sensor_data, rule, company_id)
        IF alert is NOT NULL:
            save_alert_to_database(alert)
            triggered_alerts.append(alert)
    
    RETURN triggered_alerts
```

### 4.3 Rule Matching Logic

**Matching Precedence:**
1. Exact sensor_id match (highest priority)
2. Equipment_id match (applies to all sensors on equipment)
3. Pattern match (reserved for future implementation)

**Matching Algorithm:**
```
FUNCTION filter_applicable_rules(sensor_id, equipment_id, rules):
    applicable = []
    
    FOR EACH rule IN rules:
        IF rule.enabled is FALSE:
            CONTINUE
        
        // Exact sensor match
        IF rule.sensor_id == sensor_id:
            applicable.append(rule)
            CONTINUE
        
        // Equipment match
        IF rule.equipment_id == equipment_id:
            applicable.append(rule)
            CONTINUE
    
    RETURN applicable
```

### 4.4 Threshold Evaluation

**Algorithm:**
```
FUNCTION evaluate_threshold(sensor_data, rule, company_id):
    value = sensor_data.value
    threshold_min = rule.threshold_min
    threshold_max = rule.threshold_max
    
    triggered = FALSE
    condition = NULL
    threshold_value = NULL
    
    // Check lower bound
    IF threshold_min is NOT NULL AND value < threshold_min:
        triggered = TRUE
        condition = 'below_min'
        threshold_value = threshold_min
    
    // Check upper bound
    ELSE IF threshold_max is NOT NULL AND value > threshold_max:
        triggered = TRUE
        condition = 'above_max'
        threshold_value = threshold_max
    
    IF triggered is FALSE:
        RETURN NULL
    
    // Generate alert
    alert = {
        company_id: company_id,
        rule_id: rule.rule_id,
        sensor_id: sensor_data.sensor_id,
        equipment_id: sensor_data.equipment_id,
        site_id: sensor_data.site_id,
        triggered_at: sensor_data.timestamp,
        detection_type: 'threshold',
        actual_value: value,
        threshold_value: threshold_value,
        condition: condition,
        severity: rule.severity,
        message: format_message(rule.name, value, condition)
    }
    
    RETURN alert
```

### 4.5 ML Evaluation (Framework)

**Algorithm Structure:**
```
FUNCTION evaluate_ml(sensor_data, rule, company_id):
    model_id = rule.model_id
    
    IF model_id is NULL:
        RETURN NULL
    
    // Load model (lazy loading with caching)
    IF model_id NOT IN ml_detectors:
        model = load_model_from_file(model_id)
        ml_detectors[model_id] = model
    
    detector = ml_detectors[model_id]
    
    // Prepare features (implementation-specific)
    features = prepare_features(sensor_data)
    
    // Predict anomaly
    prediction = detector.predict(features)
    anomaly_score = prediction.anomaly_score
    
    // Check threshold
    IF anomaly_score < rule.anomaly_threshold:
        RETURN NULL
    
    // Generate alert
    alert = {
        company_id: company_id,
        rule_id: rule.rule_id,
        sensor_id: sensor_data.sensor_id,
        equipment_id: sensor_data.equipment_id,
        triggered_at: sensor_data.timestamp,
        detection_type: 'ml',
        actual_value: sensor_data.value,
        anomaly_score: anomaly_score,
        model_id: model_id,
        severity: rule.severity,
        message: format_ml_message(rule.name, anomaly_score)
    }
    
    RETURN alert
```

---

## 5. Message Processing Pipeline

### 5.1 Message Flow

```
┌────────────────┐
│  Kafka Topic   │
│ sensor-data    │
└────────┬───────┘
         │
         ▼
┌────────────────────┐
│ Kafka Consumer     │
│ - Deserialize      │
│ - Validate         │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│ Extract company_id │
└────────┬───────────┘
         │
         ▼
┌────────────────────┐
│  Rules Engine      │
│ - Match rules      │
│ - Evaluate         │
└────────┬───────────┘
         │
         ├─────────────────┐
         ▼                 ▼
┌────────────────┐  ┌──────────────┐
│ Save to DB     │  │ Publish to   │
│ (schema route) │  │ Kafka alerts │
└────────────────┘  └──────────────┘
```

### 5.2 Message Format

**Input Message (Sensor Data):**
```json
{
  "company_id": "<uuid>",
  "equipment_id": "<uuid>",
  "sensor_id": "<uuid>",
  "site_id": "<string>",
  "value": <float>,
  "timestamp": "<iso8601>",
  "unit": "<string>"
}
```

**Output Message (Alert):**
```json
{
  "alert_id": "<uuid>",
  "company_id": "<uuid>",
  "rule_id": "<uuid>",
  "sensor_id": "<uuid>",
  "equipment_id": "<uuid>",
  "triggered_at": "<iso8601>",
  "detection_type": "threshold|ml",
  "severity": "low|medium|high|critical",
  "actual_value": <float>,
  "threshold_value": <float>,
  "anomaly_score": <float>,
  "message": "<string>"
}
```

### 5.3 Consumer Configuration

**Kafka Consumer Parameters:**
- `bootstrap_servers`: Kafka broker addresses
- `group_id`: Consumer group for load balancing
- `auto_offset_reset`: 'latest' (skip historical data)
- `enable_auto_commit`: true (automatic offset management)
- `value_deserializer`: JSON deserialization

**Consumption Strategy:**
```python
WHILE service_running:
    messages = consumer.getmany(
        timeout_ms=1000,
        max_records=10
    )
    
    IF messages is EMPTY:
        sleep(0.1)
        CONTINUE
    
    FOR EACH partition_messages IN messages:
        FOR EACH message IN partition_messages:
            process_message(message.value)
```

### 5.4 Error Handling

**Message Level:**
- Invalid JSON → Log error, continue processing
- Missing company_id → Log warning, skip message
- No matching rules → Debug log, continue
- Database error → Log error, continue (at-least-once delivery)

**Service Level:**
- Kafka connection loss → Automatic reconnection
- Database pool exhaustion → Backpressure (slow consumption)
- Fatal errors → Graceful shutdown, container restart

---

## 6. Database Schema

### 6.1 Tenant Schema Structure

Each tenant schema contains identical table definitions:

**alert_rules Table:**
```sql
CREATE TABLE alert_rules (
    rule_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    sensor_id UUID,
    equipment_id UUID,
    sensor_pattern TEXT,
    site_id TEXT,
    detection_type TEXT NOT NULL DEFAULT 'threshold',
    condition TEXT,
    threshold DOUBLE PRECISION,
    threshold_min DOUBLE PRECISION,
    threshold_max DOUBLE PRECISION,
    model_id UUID,
    anomaly_threshold DOUBLE PRECISION DEFAULT 0.85,
    model_config JSONB,
    requires_complete_batch BOOLEAN DEFAULT false,
    min_batch_completeness DOUBLE PRECISION DEFAULT 1.0,
    severity TEXT NOT NULL DEFAULT 'medium',
    priority INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by TEXT,
    
    CONSTRAINT alert_rules_detection_type_check 
        CHECK (detection_type IN ('threshold', 'ml', 'statistical')),
    CONSTRAINT alert_rules_severity_check 
        CHECK (severity IN ('low', 'medium', 'high', 'critical'))
);

CREATE INDEX idx_alert_rules_enabled 
    ON alert_rules(enabled) WHERE enabled = true;
```

**alerts Table:**
```sql
CREATE TABLE alerts (
    alert_id UUID DEFAULT gen_random_uuid(),
    triggered_at TIMESTAMPTZ NOT NULL,
    rule_id UUID,
    sensor_id UUID,
    equipment_id UUID,
    site_id TEXT,
    detection_type TEXT NOT NULL,
    threshold_value DOUBLE PRECISION,
    actual_value DOUBLE PRECISION,
    condition TEXT,
    model_id UUID,
    anomaly_score DOUBLE PRECISION,
    affected_sensors UUID[],
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    acknowledged BOOLEAN DEFAULT false,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (alert_id, triggered_at)
);

CREATE INDEX alerts_triggered_at_idx 
    ON alerts(triggered_at DESC);

CREATE INDEX idx_alerts_unacknowledged 
    ON alerts(triggered_at DESC) WHERE acknowledged = false;

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('alerts', 'triggered_at');
```

### 6.2 Schema Routing Queries

**Rule Loading:**
```sql
-- Set tenant schema
SET search_path TO tenant_<normalized_uuid>, public;

-- Load rules (no company_id filter needed)
SELECT 
    rule_id,
    name,
    sensor_id,
    equipment_id,
    detection_type,
    threshold_min,
    threshold_max,
    model_id,
    anomaly_threshold,
    severity,
    enabled
FROM alert_rules
WHERE enabled = true
ORDER BY priority DESC, created_at ASC;
```

**Alert Persistence:**
```sql
-- Set tenant schema
SET search_path TO tenant_<normalized_uuid>, public;

-- Insert alert (no company_id column)
INSERT INTO alerts (
    alert_id,
    triggered_at,
    rule_id,
    sensor_id,
    equipment_id,
    site_id,
    detection_type,
    threshold_value,
    actual_value,
    condition,
    model_id,
    anomaly_score,
    severity,
    message,
    acknowledged
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
);
```

### 6.3 Tenant Discovery

**Schema Enumeration:**
```sql
SELECT schema_name 
FROM information_schema.schemata 
WHERE schema_name LIKE 'tenant_%'
ORDER BY schema_name;
```

**UUID Extraction:**
```
Input:  tenant_550e8400_e29b_41d4_a716_446655440000
Step 1: Remove 'tenant_' prefix → 550e8400_e29b_41d4_a716_446655440000
Step 2: Replace underscores with hyphens → 550e8400-e29b-41d4-a716-446655440000
Output: 550e8400-e29b-41d4-a716-446655440000
```

---

## 7. Component Interfaces

### 7.1 Repository Layer

**AlertRepository Interface:**
```python
CLASS AlertRepository:
    CONSTRUCTOR(db_pool):
        self.pool = db_pool
    
    ASYNC METHOD get_active_rules(company_id) -> List[Dict]:
        schema_name = normalize_company_id_to_schema(company_id)
        SET search_path TO schema_name
        EXECUTE query
        RETURN rules
    
    ASYNC METHOD save_alert(alert_data) -> str:
        company_id = alert_data['company_id']
        schema_name = normalize_company_id_to_schema(company_id)
        SET search_path TO schema_name
        INSERT INTO alerts
        RETURN alert_id
    
    ASYNC METHOD get_tenant_schemas() -> List[str]:
        QUERY information_schema
        RETURN schema_names
    
    METHOD get_company_id_from_schema(schema_name) -> str:
        EXTRACT uuid from schema_name
        RETURN normalized_uuid
```

**RuleRepository Interface:**
```python
CLASS RuleRepository:
    CONSTRUCTOR(db_pool):
        self.pool = db_pool
    
    ASYNC METHOD load_all_tenant_rules() -> Dict[str, List[Dict]]:
        schemas = get_tenant_schemas()
        all_rules = {}
        
        FOR schema IN schemas:
            company_id = extract_company_id(schema)
            rules = get_active_rules(company_id)
            all_rules[company_id] = rules
        
        RETURN all_rules
```

### 7.2 Rules Engine Interface

```python
CLASS RulesEngine:
    CONSTRUCTOR(tenant_rules, alert_repo):
        self.tenant_rules = tenant_rules
        self.alert_repo = alert_repo
        self.ml_detectors = {}
    
    METHOD update_rules(tenant_rules):
        self.tenant_rules = tenant_rules
    
    ASYNC METHOD evaluate(sensor_data, company_id) -> List[Dict]:
        rules = self.tenant_rules[company_id]
        applicable = filter_applicable_rules(sensor_data, rules)
        
        triggered_alerts = []
        FOR rule IN applicable:
            alert = evaluate_rule(sensor_data, rule, company_id)
            IF alert:
                save_alert(alert)
                triggered_alerts.append(alert)
        
        RETURN triggered_alerts
    
    PRIVATE METHOD filter_applicable_rules(sensor_data, rules):
        RETURN rules matching sensor_id OR equipment_id
    
    PRIVATE METHOD evaluate_threshold(sensor_data, rule, company_id):
        RETURN alert IF threshold violated ELSE NULL
    
    PRIVATE ASYNC METHOD evaluate_ml(sensor_data, rule, company_id):
        RETURN alert IF anomaly detected ELSE NULL
```

### 7.3 Kafka Consumer Interface

```python
CLASS AlertKafkaConsumer:
    CONSTRUCTOR(config, rules_engine):
        self.config = config
        self.rules_engine = rules_engine
        self.consumer = None
        self.producer = None
        self.running = False
    
    ASYNC METHOD start():
        CREATE consumer
        CREATE producer
        START consuming
    
    ASYNC METHOD consume_messages():
        WHILE running:
            messages = consumer.getmany()
            FOR message IN messages:
                process_message(message.value)
    
    PRIVATE ASYNC METHOD process_message(sensor_data):
        company_id = sensor_data['company_id']
        alerts = rules_engine.evaluate(sensor_data, company_id)
        FOR alert IN alerts:
            publish_alert(alert)
    
    PRIVATE ASYNC METHOD publish_alert(alert):
        producer.send(alert_topic, alert)
    
    ASYNC METHOD stop():
        STOP consumer
        STOP producer
```

### 7.4 Service Manager Interface

```python
CLASS AlertService:
    CONSTRUCTOR():
        self.db_pool = None
        self.rules_engine = None
        self.kafka_consumer = None
        self.running = False
    
    ASYNC METHOD initialize():
        CREATE database pool
        LOAD rules from all tenants
        CREATE rules engine
        CREATE kafka consumer
        START consumer
    
    PRIVATE ASYNC METHOD load_rules():
        rule_repo = RuleRepository(db_pool)
        tenant_rules = rule_repo.load_all_tenant_rules()
        
        IF rules_engine EXISTS:
            rules_engine.update_rules(tenant_rules)
        ELSE:
            rules_engine = RulesEngine(tenant_rules, alert_repo)
    
    PRIVATE ASYNC METHOD periodic_reload():
        WHILE running:
            SLEEP(reload_interval)
            load_rules()
    
    ASYNC METHOD run():
        START periodic_reload task
        START consumer task
        WAIT for tasks
    
    ASYNC METHOD shutdown():
        STOP all tasks
        CLOSE consumer
        CLOSE database pool
```

---

## 8. Algorithms

### 8.1 UUID Normalization

**Purpose:** Convert UUID to valid PostgreSQL schema name.

**Algorithm:**
```
FUNCTION normalize_company_id_to_schema(company_id):
    INPUT: company_id (UUID string with hyphens)
           Example: "550e8400-e29b-41d4-a716-446655440000"
    
    STEP 1: Replace hyphens with underscores
            clean_id = company_id.replace('-', '_')
            Result: "550e8400_e29b_41d4_a716_446655440000"
    
    STEP 2: Prepend tenant prefix
            schema_name = "tenant_" + clean_id
            Result: "tenant_550e8400_e29b_41d4_a716_446655440000"
    
    OUTPUT: schema_name (valid PostgreSQL identifier)
    
    CONSTRAINT: PostgreSQL schema names must:
                - Start with letter or underscore
                - Contain only alphanumeric and underscores
                - Be case-insensitive
```

**Reverse Algorithm:**
```
FUNCTION get_company_id_from_schema(schema_name):
    INPUT: schema_name (PostgreSQL schema identifier)
           Example: "tenant_550e8400_e29b_41d4_a716_446655440000"
    
    STEP 1: Remove tenant prefix
            uuid_part = schema_name.replace('tenant_', '')
            Result: "550e8400_e29b_41d4_a716_446655440000"
    
    STEP 2: Replace underscores with hyphens
            company_id = uuid_part.replace('_', '-')
            Result: "550e8400-e29b-41d4-a716-446655440000"
    
    OUTPUT: company_id (UUID string)
```

### 8.2 Rule Matching

**Purpose:** Identify which rules apply to incoming sensor data.

**Algorithm:**
```
FUNCTION filter_applicable_rules(sensor_id, equipment_id, rules):
    INPUT: 
        sensor_id (UUID)
        equipment_id (UUID)
        rules (List of rule objects)
    
    OUTPUT:
        applicable_rules (List of rule objects)
    
    INITIALIZE:
        applicable_rules = empty list
        sensor_uuid = parse_uuid(sensor_id)
        equipment_uuid = parse_uuid(equipment_id)
    
    FOR EACH rule IN rules:
        // Skip disabled rules
        IF rule.enabled is FALSE:
            CONTINUE
        
        // Priority 1: Exact sensor match
        IF rule.sensor_id is NOT NULL:
            rule_sensor_uuid = parse_uuid(rule.sensor_id)
            IF rule_sensor_uuid == sensor_uuid:
                ADD rule to applicable_rules
                CONTINUE
        
        // Priority 2: Equipment match
        IF rule.equipment_id is NOT NULL AND equipment_uuid is NOT NULL:
            rule_equipment_uuid = parse_uuid(rule.equipment_id)
            IF rule_equipment_uuid == equipment_uuid:
                ADD rule to applicable_rules
                CONTINUE
    
    RETURN applicable_rules

COMPLEXITY: O(n) where n = number of rules for tenant
```

### 8.3 Threshold Detection

**Purpose:** Determine if sensor value violates threshold bounds.

**Algorithm:**
```
FUNCTION evaluate_threshold(sensor_data, rule, company_id):
    INPUT:
        sensor_data (object with value, timestamp, etc.)
        rule (object with threshold_min, threshold_max, severity)
        company_id (UUID string)
    
    OUTPUT:
        alert (object) OR NULL
    
    EXTRACT:
        value = sensor_data.value
        threshold_min = rule.threshold_min
        threshold_max = rule.threshold_max
    
    INITIALIZE:
        triggered = FALSE
        condition = NULL
        threshold_value = NULL
    
    // Lower bound check
    IF threshold_min is NOT NULL AND value < threshold_min:
        triggered = TRUE
        condition = 'below_min'
        threshold_value = threshold_min
    
    // Upper bound check (exclusive with lower bound)
    ELSE IF threshold_max is NOT NULL AND value > threshold_max:
        triggered = TRUE
        condition = 'above_max'
        threshold_value = threshold_max
    
    // No violation
    IF triggered is FALSE:
        RETURN NULL
    
    // Construct alert
    alert = {
        company_id: company_id,
        rule_id: rule.rule_id,
        sensor_id: sensor_data.sensor_id,
        equipment_id: sensor_data.equipment_id,
        site_id: sensor_data.site_id,
        triggered_at: sensor_data.timestamp,
        detection_type: 'threshold',
        actual_value: value,
        threshold_value: threshold_value,
        condition: condition,
        severity: rule.severity,
        message: format_message(rule.name, value, condition)
    }
    
    RETURN alert

COMPLEXITY: O(1)
```

### 8.4 Tenant Discovery

**Purpose:** Enumerate all tenant schemas at service startup.

**Algorithm:**
```
FUNCTION discover_tenant_schemas(db_pool):
    INPUT: db_pool (database connection pool)
    OUTPUT: tenant_map (mapping of company_id to schema_name)
    
    QUERY database:
        SELECT schema_name 
        FROM information_schema.schemata 
        WHERE schema_name LIKE 'tenant_%'
        ORDER BY schema_name
    
    INITIALIZE:
        tenant_map = empty dictionary
    
    FOR EACH schema_name IN query_results:
        company_id = get_company_id_from_schema(schema_name)
        tenant_map[company_id] = schema_name
    
    RETURN tenant_map

COMPLEXITY: O(n) where n = number of tenant schemas
EXECUTES: Once at startup, results cached in memory
```

### 8.5 Batch Rule Loading

**Purpose:** Load rules from all tenant schemas efficiently.

**Algorithm:**
```
FUNCTION load_all_tenant_rules(db_pool):
    INPUT: db_pool (database connection pool)
    OUTPUT: tenant_rules (mapping of company_id to rule list)
    
    // Discover tenants
    tenant_schemas = discover_tenant_schemas(db_pool)
    
    INITIALIZE:
        tenant_rules = empty dictionary
    
    // Load rules per tenant
    FOR EACH (company_id, schema_name) IN tenant_schemas:
        TRY:
            // Set schema context
            EXECUTE: SET search_path TO schema_name, public
            
            // Load rules
            QUERY:
                SELECT * FROM alert_rules
                WHERE enabled = true
                ORDER BY priority DESC, created_at ASC
            
            // Store results
            tenant_rules[company_id] = query_results
            
        CATCH error:
            LOG error for company_id
            tenant_rules[company_id] = empty list
    
    RETURN tenant_rules

COMPLEXITY: O(n * m) where:
            n = number of tenants
            m = average rules per tenant

OPTIMIZATION: Uses database connection pooling
              Parallel execution possible (not implemented)
```

---

## 9. Performance Characteristics

### 9.1 Throughput Metrics

**Message Processing:**
- Target: 1000-5000 messages/second per service instance
- Actual: Depends on rule complexity and count
- Bottleneck: Database alert writes (if high alert rate)

**Rule Evaluation:**
- Threshold rules: O(1) per rule
- ML rules: O(k) where k = model complexity
- Total: O(n * r) where n = applicable rules, r = rule complexity

**Database Operations:**
- Rule loading: Once per reload interval (default 60s)
- Alert writes: One INSERT per triggered alert
- Connection pool: Prevents connection overhead

### 9.2 Latency Profile

**End-to-End Latency:**
```
Message Receipt → Alert Saved
    ├─ Kafka fetch: 10-50ms
    ├─ Deserialization: <1ms
    ├─ Rule matching: <1ms
    ├─ Threshold eval: <1ms
    ├─ ML eval: 5-50ms (if applicable)
    ├─ Database write: 5-20ms
    └─ Kafka publish: 5-15ms

Total: 26-137ms (threshold)
Total: 31-187ms (ML)
```

**P95 Latency Target:** <200ms
**P99 Latency Target:** <500ms

### 9.3 Resource Requirements

**Memory:**
- Base: 50-100 MB
- Rules cache: ~1 KB per rule × rule count
- Kafka buffers: 50-100 MB
- Database pool: 10 connections × 10 MB = 100 MB
- **Total:** 200-400 MB typical

**CPU:**
- Idle: <5%
- Light load (100 msg/s): 10-20%
- Heavy load (1000 msg/s): 40-60%
- ML evaluation: +10-30% per active model

**Database Connections:**
- Minimum pool size: 5
- Maximum pool size: 10
- Rule loading: 1 connection per tenant (sequential)
- Alert writes: 1 connection per transaction

### 9.4 Scalability Limits

**Vertical Scaling:**
- Limited by single-thread Kafka consumer
- Can increase via worker count (multiple containers)

**Horizontal Scaling:**
- Kafka consumer group partitioning
- Each instance handles subset of partitions
- Stateless design enables easy scaling

**Tenant Scaling:**
- Memory: O(n) where n = total rule count
- Startup time: O(m) where m = tenant count
- No practical limit on tenant count

---

## 10. Deployment Specifications

### 10.1 Container Configuration

**Base Image:**
```dockerfile
FROM python:3.11-slim
```

**System Dependencies:**
- gcc, g++ (for asyncpg compilation)
- libpq-dev (PostgreSQL client library)

**Python Dependencies:**
- aiokafka==0.10.0 (async Kafka client)
- python-snappy==0.7.2 (Kafka compression)
- asyncpg>=0.29.0 (async PostgreSQL driver)
- python-dateutil>=2.8.2 (timestamp parsing)

**Working Directory:** `/app`

**Entry Point:** `python main.py`

### 10.2 Environment Variables

**Required:**
- `ALERT_SERVICE_DB_USER`: Database role name
- `ALERT_SERVICE_DB_PASSWORD`: Database password
- `KAFKA_BOOTSTRAP_SERVERS`: Kafka broker list
- `KAFKA_TOPIC_SENSOR_DATA`: Input topic name
- `KAFKA_TOPIC_ALERTS`: Output topic name
- `KAFKA_GROUP_ID`: Consumer group identifier

**Optional (with defaults):**
- `DB_HOST`: Database hostname (default: timescaledb)
- `DB_PORT`: Database port (default: 5432)
- `DB_NAME`: Database name (default: industryflow)
- `RULE_RELOAD_INTERVAL`: Seconds between reloads (default: 60)
- `LOG_LEVEL`: Logging verbosity (default: INFO)

### 10.3 Health Checks

**Liveness Probe:**
```bash
ps aux | grep -v grep | grep 'python main.py' || exit 1
```
- Interval: 30s
- Timeout: 10s
- Retries: 3

**Readiness Indicators:**
- Database connection established
- Rules loaded successfully
- Kafka consumer connected
- Message consumption active

### 10.4 Resource Limits

**Recommended Limits:**
```yaml
resources:
  requests:
    memory: 256Mi
    cpu: 200m
  limits:
    memory: 512Mi
    cpu: 500m
```

**Justification:**
- Memory: Accommodates rule cache + Kafka buffers
- CPU: Sufficient for 500-1000 msg/s throughput
- Adjust based on actual tenant count and rule complexity

### 10.5 Network Configuration

**Inbound:** None (consumer-only, no exposed ports)

**Outbound:**
- Kafka brokers: Port 9092 (internal) or 29092 (Docker)
- TimescaleDB: Port 5432
- No external internet access required

**Service Dependencies:**
- `timescaledb`: Database (must be healthy before start)
- `kafka`: Message broker (must be started before start)

### 10.6 Persistence Requirements

**Volumes:** None required (stateless service)

**Data Storage:**
- Rules: Read from database (no local cache persistence)
- Alerts: Written to database (no local buffering)
- ML models: Mounted read-only volume (if ML enabled)

### 10.7 Restart Policy

**Policy:** `unless-stopped`

**Behavior:**
- Automatic restart on failure
- Graceful shutdown on SIGTERM
- No restart on manual stop

**Startup Sequence:**
1. Validate configuration
2. Connect to database
3. Load rules from all tenants
4. Initialize Kafka consumer
5. Start message consumption

**Shutdown Sequence:**
1. Receive SIGTERM signal
2. Stop accepting new messages
3. Complete in-flight evaluations
4. Close Kafka connections
5. Close database pool
6. Exit cleanly

---

## Appendix A: Schema Comparison

### Row-Level Security vs Schema-per-Tenant

**Row-Level Security (Previous):**
```sql
-- Policy-based filtering
CREATE POLICY tenant_isolation ON alert_rules
    USING (company_id = current_setting('app.current_company_id')::uuid);

-- Query requires company_id column
SELECT * FROM alert_rules 
WHERE company_id = '<uuid>' AND enabled = true;

-- Manual context setting per query
SET LOCAL app.current_company_id = '<uuid>';
```

**Schema-per-Tenant (Current):**
```sql
-- No policies needed
-- No company_id column in tenant tables

-- Query without company_id
SELECT * FROM alert_rules WHERE enabled = true;

-- Automatic routing via search path
SET search_path TO tenant_<normalized_uuid>, public;
```

**Key Differences:**

| Aspect | RLS | Schema-per-Tenant |
|--------|-----|-------------------|
| Isolation Method | Policies + session vars | Schema namespaces |
| Query Complexity | Higher (manual filtering) | Lower (automatic routing) |
| Performance | Policy evaluation overhead | Direct schema access |
| Compression | Incompatible | Compatible |
| Maintenance | Complex policies | Simple grants |
| Tenant Onboarding | Add row in table | Create schema |

---

## Appendix B: Error Scenarios

### B.1 Database Connection Loss

**Detection:** Connection pool raises exception on acquire

**Handling:**
```
TRY:
    connection = pool.acquire()
    execute_query()
CATCH connection_error:
    LOG error with context
    WAIT exponential_backoff
    RETRY with limit
    IF max_retries_exceeded:
        INITIATE graceful_shutdown
```

### B.2 Kafka Broker Unavailable

**Detection:** Consumer fails to fetch messages

**Handling:**
- aiokafka handles automatic reconnection
- Consumer pauses until broker available
- No message loss (offset-based tracking)

### B.3 Invalid Message Format

**Detection:** JSON deserialization fails or missing required fields

**Handling:**
```
TRY:
    sensor_data = parse_json(message.value)
    validate_required_fields(sensor_data)
    process_message(sensor_data)
CATCH parse_error:
    LOG error with raw message
    SKIP message
    CONTINUE processing
```

### B.4 Tenant Schema Not Found

**Detection:** SET search_path fails or query fails

**Handling:**
```
TRY:
    SET search_path TO tenant_<uuid>
    QUERY alert_rules
CATCH schema_not_found:
    LOG error with company_id
    SKIP message
    NOTIFY ops team
```

### B.5 Rule Evaluation Failure

**Detection:** Exception during threshold or ML evaluation

**Handling:**
```
FOR EACH rule IN applicable_rules:
    TRY:
        alert = evaluate_rule(sensor_data, rule)
        save_alert(alert)
    CATCH evaluation_error:
        LOG error with rule_id
        CONTINUE to next rule
```

---

## Appendix C: Monitoring Recommendations

### C.1 Key Metrics

**Throughput Metrics:**
- `messages_consumed_total`: Counter of processed messages
- `messages_consumed_rate`: Messages per second
- `alerts_generated_total`: Counter of triggered alerts
- `alerts_generated_rate`: Alerts per second

**Latency Metrics:**
- `message_processing_duration`: Histogram (p50, p95, p99)
- `rule_evaluation_duration`: Histogram per detection type
- `database_query_duration`: Histogram per operation

**Error Metrics:**
- `message_processing_errors_total`: Counter
- `database_connection_errors_total`: Counter
- `kafka_connection_errors_total`: Counter

**Resource Metrics:**
- `database_connections_active`: Gauge
- `memory_usage_bytes`: Gauge
- `cpu_usage_percent`: Gauge

### C.2 Alert Conditions

**Critical:**
- Service down for >5 minutes
- Database connection pool exhausted
- Kafka consumer lag >10000 messages

**Warning:**
- Error rate >1% of messages
- P95 latency >500ms
- Memory usage >80% of limit

**Info:**
- Service restart
- Configuration reload
- Zero alerts for >1 hour (may indicate issue)

---

**END OF DOCUMENTATION**