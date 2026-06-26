# IndustryFlow — Getting Started & Operations Guide

> ⚠️ **Pending verification.** This guide was relocated from the project README during the
> repository cleanup. Its commands, ports, credentials, and benchmarks predate the code-baseline
> cleanup and have **not yet been reconciled with the code** (see the review and the ADR process).
> It is preserved here as-is and will be corrected once the code baseline is reached. Treat
> specific values as indicative, not authoritative — the authoritative homes are
> `docker-compose.yml`, `.env.example`, and the SQL init scripts (per ADR-0000).

---

## Prerequisites

- Docker & Docker Compose
- 8 GB+ RAM recommended
- Ports available: 3000, 3001, 5432, 8000–8002, 8888, 9090 (and supporting infra ports)

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

## 3. Access interfaces

| Service | URL | Credentials |
|---------|-----|-------------|
| **API Gateway** | http://localhost:8000 | — |
| **Grafana** | http://localhost:3001 | admin / see `.env` |
| **Jupyter** | http://localhost:8888 | Token in logs |
| **MLflow** | http://localhost:5000 | — |
| **Prometheus** | http://localhost:9090 | — |

## 4. Stream test data

```bash
# Terminal 1: Start data streaming
cd services/mock_service
python3 stream_tep_data.py

# Terminal 2: Monitor in real-time
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
| **Ingestion Service** | Sensor data ingestion | — | Python/Kafka |
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

See [docs/MONITORING.md](MONITORING.md) for the complete metrics, logs, and dashboards guide.

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
# Unit tests
pytest services/api_gateway/tests/

# Integration tests
pytest tests/integration/

# Load tests
locust -f tests/load/locustfile.py
```

### Generate test data

```bash
cd services/mock_service
python3 stream_tep_data.py                       # Stream TEP dataset
python3 stream_tep_data.py --fault 1 --duration 300   # Stream with a specific fault
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

For monitoring issues, see [docs/MONITORING.md](MONITORING.md#troubleshooting).

---

## Performance notes

> ⚠️ The figures below are claims inherited from the original README and are **not yet
> substantiated by benchmarks in this repository**. They are retained pending verification.

- Columnar compression (TimescaleDB) for time-series storage
- Continuous aggregations — pre-computed 1 min / 5 min / 1 hour windows
- Redis-based feature store for real-time inference
- Configurable batch sizes for Spark jobs
- Async database connection pooling
