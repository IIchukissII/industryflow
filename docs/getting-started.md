<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow — Getting Started & Operations Guide

> The authoritative homes for concrete values are `docker-compose.yml`, `.env.example`, and
> the SQL init scripts (per [ADR-0000](../ADR/ADR-0000-decision-records-and-source-of-truth.md));
> this guide references them. For the auth model see [authentication](operations/authentication.md).

---

## Prerequisites

- Docker & Docker Compose
- 8 GB+ RAM recommended
- Ports available: 3000, 3001, 5000, 5432, 8000–8003, 8888, 9000–9001, 9090, 3100 (and supporting infra ports)

## 1. Clone and configure

```bash
git clone https://github.com/IIchukissII/industryflow
cd industryflow
cp .env.example .env   # then edit the values
```

## 2. Start services

```bash
# Start core infrastructure
docker-compose up -d

# Wait for services to initialize (~2 minutes)
docker-compose ps

# Check service health
curl http://localhost:8000/health
```

## 3. Create your first tenant and admin

The database starts empty — no tenants are seeded, so nothing is hardcoded. Provision a
tenant at runtime, then create an admin user in it:

```bash
# Create a tenant (prints its company_id + schema)
scripts/create-tenant.sh "Your Company"

# Put that company_id in .env, then seed the admin user
#   ADMIN_USER_1_EMAIL=admin@your-company.example
#   ADMIN_USER_1_PASSWORD=...
#   ADMIN_USER_1_COMPANY_ID=<company_id from above>
python3 scripts/seed_users.py
```

Additional users can be created by an admin through the API / admin UI.

## 4. Access interfaces

| Service | URL | Credentials |
|---------|-----|-------------|
| **API Gateway** | http://localhost:8000 | — |
| **Grafana** | http://localhost:3001 | admin / see `.env` |
| **Jupyter** | http://localhost:8888 | Token in logs |
| **MLflow** | http://localhost:5000 | — |
| **Prometheus** | http://localhost:9090 | — |

## 5. Stream test data

The reference producer (the Tennessee-Eastman example dataset) lives in the
`tep-reference` extension and ingests over **device mTLS**, so it first needs a device
certificate ([device-mtls.md](operations/device-mtls.md)):

```bash
# Issue a device cert for the producer, bound to your tenant's company_id
scripts/device-ca.sh init                                  # once
scripts/device-ca.sh issue <company_id> tep-producer

# Terminal 1: stream (data files resolve relative to the script; cert via env)
DEVICE_CERT=deploy/device-ca/devices/tep-producer/tep-producer.chain.crt \
DEVICE_KEY=deploy/device-ca/devices/tep-producer/tep-producer.key \
INGESTION_URL=https://localhost:8443/ingest \
  python3 extensions/tep-reference/producer/stream_tep_data.py

# Terminal 2: monitor
docker logs -f industryflow-spark-streaming
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Frontend (React)                            │
│                    http://localhost:3000                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                   API Gateway (FastAPI)                         │
│              Authentication & Request Routing                   │
└─────┬──────────────┬──────────────┬────────────────┬───────────┘
      │              │              │                │
┌─────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐ ┌──────▼────────┐
│  Alert     │ │   ML     │ │  Ingestion  │ │  Monitoring   │
│  Service   │ │ Service  │ │   Service   │ │    Stack      │
└─────┬──────┘ └────┬─────┘ └──────┬──────┘ └───────────────┘
      │              │              │
      └──────────────┼──────────────┘
                     │
      ┌──────────────▼──────────────┐
      │      Apache Kafka           │
      └──────────┬──────────────────┘
                 │
      ┌──────────▼──────────────────┐
      │    Apache Spark             │
      └──────────┬──────────────────┘
                 │
      ┌──────────▼──────────────────┐
      │    TimescaleDB + Redis + MinIO │
      └─────────────────────────────┘
```

### Core services

| Component | Purpose | Port | Technology |
|-----------|---------|------|------------|
| **API Gateway** | Request routing, auth | 8000 | FastAPI |
| **Ingestion Service** | Sensor data ingestion | 8003 | Python/Kafka |
| **Alert Service** | Anomaly detection | 8001 | FastAPI + PySpark |
| **ML Service** | Model training/inference | 8002 | FastAPI + MLflow |
| **Spark Streaming** | Real-time processing | — | PySpark |
| **Spark Aggregations** | Data aggregation | — | PySpark |

### Infrastructure

| Component | Purpose | Port | Details |
|-----------|---------|------|---------|
| **TimescaleDB** | Time-series database | 5432 | PostgreSQL + TimescaleDB |
| **Kafka** | Message broker | 9092 | sensor-data-raw topic |
| **Redis** | Cache & feature store | 6379 | In-memory data store |
| **MinIO** | Object storage | 9000 | S3-compatible (ML models) |
| **MLflow** | ML experiment tracking | 5000 | Model registry |
| **Zookeeper** | Kafka coordination | 2181 | Required for Kafka |

### Monitoring stack

| Component | Purpose | Port |
|-----------|---------|------|
| **Prometheus** | Metrics collection | 9090 |
| **Grafana** | Dashboards | 3001 |
| **Loki** | Log aggregation | 3100 |
| **Promtail** | Log collection | — |
| **Exporters** | cAdvisor, Node, PostgreSQL, Redis, Kafka | various |

See the [monitoring guide](operations/monitoring.md) for the complete metrics, logs, and dashboards reference.

---

## Configuration

Key variables in `.env` (authoritative list lives in `.env.example`):

```bash
# Database
DB_HOST=timescaledb
DB_PORT=5432
DB_NAME=industryflow
DB_USER=postgres
DB_PASSWORD=<secure-password>

# Services
API_GATEWAY_PORT=8000
ALERT_SERVICE_PORT=8001
ML_SERVICE_PORT=8002

# Monitoring
GRAFANA_PORT=3001
GRAFANA_ADMIN_PASSWORD=<secure-password>
PROMETHEUS_PORT=9090

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:29092
KAFKA_TOPIC_SENSOR_DATA=sensor-data-raw
KAFKA_TOPIC_ALERTS=sensor-alerts

# MLflow
MLFLOW_TRACKING_URI=http://mlflow:5000
MLFLOW_S3_ENDPOINT_URL=http://minio:9000
```

### Resource requirements

**Minimum:** 4 cores / 8 GB RAM / 50 GB disk.
**Recommended:** 8+ cores / 16 GB RAM / 100 GB SSD.

---

## API examples

### Authentication

These examples use the **API/service** bearer flow against the gateway. The browser app
authenticates differently — httpOnly cookies + CSRF over HTTPS — see
**[operations/authentication.md](operations/authentication.md)**.

```bash
curl -X POST http://localhost:8000/auth/jwt/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=<email>&password=<password>'
# Returns: {"access_token": "...", "token_type": "bearer"}
```

### Sensor data

```bash
curl http://localhost:8000/api/sensors/latest \
  -H "Authorization: Bearer <token>"

curl "http://localhost:8000/api/sensors/<sensor_id>?start_time=<iso8601>&end_time=<iso8601>" \
  -H "Authorization: Bearer <token>"
```

### Alert rules

```bash
curl http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer <token>"

curl -X POST http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "High Temperature Alert",
    "sensor_pattern": "temp_*",
    "detection_type": "threshold",
    "condition": "greater_than",
    "threshold": 80.0,
    "severity": "critical"
  }'
```

Interactive API docs: http://localhost:8000/docs (Swagger UI, when running).

---

## Testing

```bash
# Service unit tests (where present)
pytest services/ml_service/tests/
pytest services/spark_jobs/tests/

# Performance / load testing harness
python3 tests/performance/performance_test.py        # see the scripts in tests/performance/
```

### Generate test data

```bash
# Reference producer (device mTLS — see step 5 for the cert). Data resolves relative to the script.
DEVICE_CERT=deploy/device-ca/devices/tep-producer/tep-producer.chain.crt \
DEVICE_KEY=deploy/device-ca/devices/tep-producer/tep-producer.key \
  python3 extensions/tep-reference/producer/stream_tep_data.py
```

---

## Troubleshooting

**Services won't start**
```bash
docker system df
docker system prune          # if low on disk space
docker-compose logs <service-name>
docker-compose restart <service-name>
```

**Database connection errors**
```bash
docker logs industryflow-timescaledb
docker exec industryflow-timescaledb psql -U postgres -c "SELECT 1"
```

**Kafka not receiving messages**
```bash
docker exec industryflow-kafka kafka-topics --list --bootstrap-server localhost:9092
docker exec industryflow-kafka kafka-consumer-groups --list --bootstrap-server localhost:9092
```

For monitoring issues, see the [monitoring guide](operations/monitoring.md#troubleshooting).

---

## Performance notes

> ⚠️ The figures below are claims inherited from the original README and are **not yet
> substantiated by benchmarks in this repository**. They are retained pending verification.

- Columnar compression (TimescaleDB) for time-series storage
- Continuous aggregations — pre-computed 1 min / 5 min / 1 hour windows
- Redis-based feature store for real-time inference
- Configurable batch sizes for Spark jobs
- Async database connection pooling
