# IndustryFlow v2

Real-time Industrial IoT Platform for Sensor Data Processing, Anomaly Detection, and Predictive Maintenance

## Overview

IndustryFlow v2 is a comprehensive industrial monitoring platform built for processing high-velocity sensor data streams, detecting anomalies using machine learning, and providing real-time alerts for industrial equipment.

### Key Features

- **Real-time Stream Processing** - Apache Spark handles millions of sensor readings per second
- **Multi-tenant Architecture** - Schema-per-tenant isolation for secure data separation
- **ML-Powered Anomaly Detection** - XGBoost and ensemble models for fault detection
- **Scalable Storage** - TimescaleDB for efficient time-series data management
- **Comprehensive Monitoring** - Prometheus, Grafana, and Loki for full observability
- **Alert Management** - Configurable threshold and ML-based alerting system

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- 8GB+ RAM recommended
- Ports available: 3000, 3001, 5432, 8000-8002, 8888, 9090

### 1. Clone and Setup

```bash
git clone <repository-url>
cd industryflow-v2
cp .env.example .env  # Configure environment variables
```

### 2. Start Services

```bash
# Start core infrastructure
docker-compose up -d

# Wait for services to initialize (~2 minutes)
docker-compose ps

# Check service health
curl http://localhost:8000/health
```

### 3. Access Interfaces

| Service | URL | Credentials |
|---------|-----|-------------|
| **API Gateway** | http://localhost:8000 | - |
| **Grafana** | http://localhost:3001 | admin / see `.env` |
| **Jupyter** | http://localhost:8888 | Token in logs |
| **MLflow** | http://localhost:5001 | - |
| **Prometheus** | http://localhost:9090 | - |

### 4. Stream Test Data

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
│  (API +    │ │  (API +  │ │             │ │ (Prometheus + │
│  Detector) │ │  MLflow) │ │             │ │  Grafana +    │
│            │ │          │ │             │ │  Loki)        │
└─────┬──────┘ └────┬─────┘ └──────┬──────┘ └───────────────┘
      │              │              │
      └──────────────┼──────────────┘
                     │
      ┌──────────────▼──────────────┐
      │      Apache Kafka           │
      │   (Message Streaming)       │
      └──────────┬──────────────────┘
                 │
      ┌──────────▼──────────────────┐
      │    Apache Spark             │
      │ (Stream Processing +        │
      │  Aggregations)              │
      └──────────┬──────────────────┘
                 │
      ┌──────────▼──────────────────┐
      │    TimescaleDB              │
      │  (Time-Series Storage)      │
      │  + Redis (Cache)            │
      │  + MinIO (Model Storage)    │
      └─────────────────────────────┘
```

---

## System Components

### Core Services

| Component | Purpose | Port | Technology |
|-----------|---------|------|------------|
| **API Gateway** | Request routing, auth | 8000 | FastAPI |
| **Ingestion Service** | Sensor data ingestion | - | Python/Kafka |
| **Alert Service** | Anomaly detection | 8001 | FastAPI + PySpark |
| **ML Service** | Model training/inference | 8002 | FastAPI + MLflow |
| **Spark Streaming** | Real-time processing | - | PySpark |
| **Spark Aggregations** | Data aggregation | - | PySpark |

### Infrastructure

| Component | Purpose | Port | Details |
|-----------|---------|------|---------|
| **TimescaleDB** | Time-series database | 5432 | PostgreSQL + TimescaleDB extension |
| **Kafka** | Message broker | 9092 | 12 partitions for sensor-data-raw |
| **Redis** | Cache & feature store | 6379 | In-memory data store |
| **MinIO** | Object storage | 9000 | S3-compatible, stores ML models |
| **MLflow** | ML experiment tracking | 5001 | Model registry |
| **Zookeeper** | Kafka coordination | 2181 | Required for Kafka |

### Monitoring Stack

| Component | Purpose | Port | Documentation |
|-----------|---------|------|---------------|
| **Prometheus** | Metrics collection | 9090 | [MONITORING.md](docs/MONITORING.md) |
| **Grafana** | Dashboards | 3001 | [MONITORING.md](docs/MONITORING.md) |
| **Loki** | Log aggregation | 3100 | [MONITORING.md](docs/MONITORING.md) |
| **Promtail** | Log collection | - | [MONITORING.md](docs/MONITORING.md) |
| **Exporters** | Specialized metrics | Various | cAdvisor, Node, PostgreSQL, Redis, Kafka |

---

## Documentation

### User Guides

- **[Monitoring & Observability](docs/MONITORING.md)** - Complete guide to metrics, logs, and dashboards
- **[User Management](docs/USER_MANAGEMENT.md)** - User roles, permissions, and management
- **[Authentication Setup](docs/AUTHENTICATION_SETUP.md)** - JWT authentication and configuration
- **[API Documentation](http://localhost:8000/docs)** - Interactive API docs (when running)

### Architecture Documentation

**Core Architecture**:
- **[Database Architecture](docs/architecture/IndustryFlow_Database_Architecture.md)** - Schema-per-tenant design, tables, and relationships
- **[Spark Streaming Architecture](docs/architecture/Spark_Streaming_Schema_Per_Tenant_Architecture.md)** - Multi-tenant stream processing
- **[TimescaleDB Compression](docs/architecture/TimescaleDB_Compression_Technical_Specification.md)** - Columnar compression and optimization

**Service Architecture**:
- **[Alert Detection Service](docs/architecture/Alert_Detection_Service_Architecture.md)** - Anomaly detection architecture
- **[ML Service Architecture](docs/architecture/ML_Service_Architecture.md)** - Model training and management
- **[ML Inference & Feature Engineering](docs/architecture/ML_Inference_and_Feature_Engineering.md)** - Real-time inference pipeline
- **[Feature Engineering Service](docs/architecture/Feature_Engineering_Service_Architecture.md)** - Feature store and computation

### API Documentation

**Service APIs**:
- **[API Gateway](docs/api/API_Gateway_Documentation.md)** - Main API entry point and routing
- **[Alert Service API](docs/api/Alert_Service_API_Documentation.md)** - Alert rules and detection
- **[ML Service API](docs/api/ML_Service_API_Documentation.md)** - Model training and inference
- **[Feature Engineering API](docs/api/Feature_Engineering_API_Documentation.md)** - Feature configuration and computation
- **[Ingestion Service](docs/api/Ingestion_Service_Technical_Documentation.md)** - Sensor data ingestion
- **[API Endpoints Reference](docs/api/api_endpoints.md)** - Complete endpoint listing

### Test Reports

- **[Feature Engineering Service Tests](docs/test-reports/Feature_Engineering_Service_Test_Report.md)** - Comprehensive test results

### Infrastructure

- **[TimescaleDB Init Scripts](infrastructure/timescaledb/init-scripts/README.md)** - Database initialization documentation
- **[ML Notebooks Utils](services/ml_service/notebooks/utils/README.md)** - Jupyter notebook utilities

---

## Development

### Project Structure

```
industryflow-v2/
├── services/
│   ├── api_gateway/          # Main API entry point
│   ├── alert_service/        # Anomaly detection service
│   ├── ml_service/           # ML training and inference
│   ├── ingestion_service/    # Sensor data ingestion
│   ├── spark/                # Spark streaming jobs
│   ├── frontend/             # React UI
│   └── mock_service/         # Test data generation
├── infrastructure/
│   ├── prometheus/           # Prometheus configuration
│   ├── grafana/              # Grafana dashboards
│   ├── loki/                 # Loki configuration
│   └── promtail/             # Promtail configuration
├── docs/                     # Documentation
├── docker-compose.yml        # Service orchestration
└── .env                      # Environment configuration
```

### Technology Stack

**Backend**:
- Python 3.11
- FastAPI (REST APIs)
- Apache Spark 3.5 (Stream processing)
- Apache Kafka (Message streaming)
- MLflow (ML lifecycle)

**Data Storage**:
- PostgreSQL 15 + TimescaleDB 2.11 (Time-series)
- Redis 7 (Caching)
- MinIO (Object storage)

**ML/AI**:
- XGBoost, Random Forest (Anomaly detection)
- Scikit-learn (Preprocessing)
- Feature store (Redis-based)

**Monitoring**:
- Prometheus (Metrics)
- Grafana (Visualization)
- Loki (Logs)

**Frontend**:
- React 18
- Modern dashboard UI

---

## Configuration

### Environment Variables

Key variables in `.env`:

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

### Resource Requirements

**Minimum**:
- CPU: 4 cores
- RAM: 8 GB
- Disk: 50 GB

**Recommended**:
- CPU: 8+ cores
- RAM: 16 GB
- Disk: 100 GB SSD

---

## Monitoring & Observability

### Dashboards

Access Grafana at **http://localhost:3001** (default credentials in `.env`)

**Available Dashboards**:
1. **Complete System Infrastructure** - Service health, resources, metrics
2. **Container Logs** - Centralized log viewer with filtering

### Metrics

Prometheus collects metrics from:
- All FastAPI services (request rates, latencies, errors)
- Container resources (CPU, memory, network)
- System resources (host CPU, memory, disk)
- Database (connections, queries, sizes)
- Kafka (topics, consumer lag, throughput)
- Redis (memory, hit rates, commands)

**See**: [docs/MONITORING.md](docs/MONITORING.md) for complete metrics guide

### Logs

Loki aggregates logs from 23+ containers with:
- Real-time streaming
- Full-text search
- Label-based filtering
- 7-day retention

**See**: [docs/MONITORING.md](docs/MONITORING.md#logs) for query examples

---

## API Endpoints

### Authentication

```bash
# Login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'

# Returns: {"access_token": "...", "token_type": "bearer"}
```

### Sensor Data

```bash
# Get latest sensor readings
curl http://localhost:8000/api/sensors/latest \
  -H "Authorization: Bearer <token>"

# Get sensor measurements with time range
curl "http://localhost:8000/api/sensors/<sensor_id>?start_time=<iso8601>&end_time=<iso8601>" \
  -H "Authorization: Bearer <token>"
```

### Alert Rules

```bash
# List all alert rules
curl http://localhost:8001/api/alert-rules \
  -H "Authorization: Bearer <token>"

# Create threshold rule
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

**Full API documentation**: http://localhost:8000/docs (interactive Swagger UI)

---

## Testing

### Run Tests

```bash
# Unit tests
pytest services/api_gateway/tests/

# Integration tests
pytest tests/integration/

# Load tests
locust -f tests/load/locustfile.py
```

### Generate Test Data

```bash
# Stream TEP (Tennessee Eastman Process) dataset
cd services/mock_service
python3 stream_tep_data.py

# Stream with specific fault
python3 stream_tep_data.py --fault 1 --duration 300
```

---

## Troubleshooting

### Common Issues

**Services won't start**:
```bash
# Check Docker resources
docker system df
docker system prune  # If low on disk space

# Check logs
docker-compose logs <service-name>

# Restart specific service
docker-compose restart <service-name>
```

**Database connection errors**:
```bash
# Check TimescaleDB is ready
docker logs industryflow-timescaledb

# Test connection
docker exec industryflow-timescaledb psql -U postgres -c "SELECT 1"
```

**Kafka not receiving messages**:
```bash
# Check Kafka topics
docker exec industryflow-kafka kafka-topics --list \
  --bootstrap-server localhost:9092

# Check consumer lag
docker exec industryflow-kafka kafka-consumer-groups --list \
  --bootstrap-server localhost:9092
```

**For monitoring issues**: See [docs/MONITORING.md#troubleshooting](docs/MONITORING.md#troubleshooting)

---

## Performance

### Optimizations Implemented

- **Columnar compression** - Gorilla algorithm for TimescaleDB (30x compression)
- **Continuous aggregations** - Pre-computed 1min, 5min, 1hour windows
- **Feature store** - Redis-based caching for real-time inference
- **Batch processing** - Configurable batch sizes for Spark jobs
- **Connection pooling** - Async database pools for all services

### Benchmarks

- **Ingestion throughput**: 10,000+ events/second
- **Query latency (p95)**: < 100ms for latest readings
- **ML inference**: < 50ms per batch
- **Alert detection**: < 1 second from event to alert

---

## Contributing

### Development Workflow

1. Create feature branch from `v2`
2. Make changes following code style
3. Add tests for new functionality
4. Update relevant documentation
5. Submit pull request

### Code Style

- Python: PEP 8 (enforced by `black` and `flake8`)
- SQL: Lowercase keywords, snake_case identifiers
- Git commits: Conventional commits format

---

## License

[Add license information]

---

## Support

- **Issues**: [GitHub Issues](https://github.com/your-org/industryflow/issues)
- **Discussions**: [GitHub Discussions](https://github.com/your-org/industryflow/discussions)
- **Documentation**: [docs/](docs/)

---

## Acknowledgments

Built with:
- Apache Spark, Kafka, TimescaleDB
- FastAPI, React
- Prometheus, Grafana, Loki
- MLflow, XGBoost, Scikit-learn
