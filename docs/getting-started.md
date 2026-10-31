<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Getting started

Running the Docker Compose stack: configuration, first tenant and admin, components and ports, tests.
Values live in [`docker-compose.yml`](../docker-compose.yml) and [`.env.example`](../.env.example).

## Prerequisites

| Requirement | Used by |
|---|---|
| Docker with Compose v2 | The stack |
| `openssl` on the host | `scripts/device-ca.sh`, `scripts/gen-internal-ca.sh` |
| Python 3 with `requests` and `pandas` | The reference data producer |
| 8 GB+ RAM | The stack |

## 1. Configure

```bash
git clone https://github.com/IIchukissII/industryflow
cd industryflow
cp .env.example .env
```

| Variables | Action |
|---|---|
| Every `changeme_*` value (DB roles, MinIO principals, `JWT_SECRET_KEY`, `INTERNAL_SERVICE_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, …) | Replace with generated secrets |
| `FRONTEND_HTTPS_PORT`, `FRONTEND_PORT`, `INGESTION_MTLS_PORT` | Published ports ([components](#components)) |
| `CERT_EXTRA_SAN`, `TLS_CERT_DIR` | Edge certificate ([TLS](operations/tls.md)) |
| `DEVICE_CA_DIR`, `DEVICE_CA_UID`, `DEVICE_CA_GID` | Device CA location and owner ([device mTLS](operations/device-mtls.md)) |
| `ADMIN_USER_<n>_*` | First users (step 4) |
| `DOCKER_GID`, `JUPYTERHUB_CRYPT_KEY`, `NOTEBOOK_SSO_PROXY_SECRET`, `NOTEBOOK_SQL_PROXY_*` | Notebooks profile only (step 6) |

## 2. Initialise the device CA

`ingestion-edge` and `device-crl-refresh` do not start without it.

```bash
scripts/device-ca.sh init
```

## 3. Start the stack

```bash
docker compose up -d
docker compose ps
curl http://localhost:8000/health
```

## 4. Create the first tenant and admin

The database starts with no tenants.

```bash
scripts/create-tenant.sh "Your Company"     # prints the company_id

# In .env: ADMIN_USER_1_EMAIL, ADMIN_USER_1_PASSWORD, ADMIN_USER_1_COMPANY_ID=<company_id>
set -a; . ./.env; set +a
docker compose run --rm --no-deps -v ./scripts:/scripts:ro -e PYTHONPATH=/app \
  -e ADMIN_USER_1_EMAIL -e ADMIN_USER_1_PASSWORD -e ADMIN_USER_1_COMPANY_ID -e ADMIN_USER_1_ROLE \
  -e ADMIN_USER_1_SUPERUSER \
  api-gateway python /scripts/seed_users.py
```

`-e NAME` copies the variable from the shell, so the password is never an argument. Seeder variables,
further users and tenants: [user management](operations/user-management.md).

Open `https://localhost:<FRONTEND_HTTPS_PORT>` and sign in. From another machine:
[name resolution](operations/tls.md#name-resolution).

## 5. Stream test data

```bash
scripts/device-ca.sh issue <company_id> tep-producer

# Terminal 1: stream
DEVICE_CERT=deploy/device-ca/devices/<company_id>/tep-producer/current.chain.crt \
DEVICE_KEY=deploy/device-ca/devices/<company_id>/tep-producer/current.key \
  python3 extensions/tep-reference/producer/stream_tep_data.py

# Terminal 2: watch the streaming job
docker logs -f industryflow-spark-streaming
```

Producer settings: [tep-reference](../extensions/tep-reference/README.md#producer-settings).

## 6. Notebooks (optional profile)

```bash
# In .env: DOCKER_GID=$(getent group docker | cut -d: -f3), JUPYTERHUB_CRYPT_KEY and
# NOTEBOOK_SSO_PROXY_SECRET (openssl rand -hex 32 each)
docker compose --profile notebooks build
docker compose --profile notebooks up -d
```

Open `https://localhost:<FRONTEND_HTTPS_PORT>/jupyter/` after signing in. The notebook hub spawns
per-user containers through the Docker socket ([notebooks](architecture/notebooks.md)).

## Components

Host ports bind to `127.0.0.1`, except `frontend` and `ingestion-edge`, which bind every interface.
Grafana can also be served by the edge at `/grafana/` ([monitoring](operations/monitoring.md#grafana)).
From another machine, reach a loopback port through an SSH tunnel (`ssh -L 5000:localhost:5000 <host>`).

| Name | Role | Port | Config |
|---|---|---|---|
| `frontend` | HTTPS edge, React app, same-origin proxy ([edge routes](api/README.md#edge-routes)) | `FRONTEND_HTTPS_PORT`; `FRONTEND_PORT` redirects to HTTPS | `services/frontend/` |
| `api-gateway` | Auth, tenant data, alert rules and history, websocket ([API](api/README.md)) | `API_GATEWAY_PORT` | `services/api_gateway/` |
| `ml-service-api` | Models, feature configs, inference, drift ([ML](architecture/ml-and-features.md)) | `ML_SERVICE_PORT` | `services/ml_service/` |
| `ingestion-edge` | Device mTLS termination, CRL check ([device mTLS](operations/device-mtls.md)) | `INGESTION_MTLS_PORT` | `services/ingestion_edge/nginx.conf` |
| `ingestion-service` | Produces readings to Kafka; reachable only from the edge | none | `services/ingestion_service/` |
| `device-crl-refresh` | Re-signs the device CRL | none | `scripts/device-ca.sh` |
| `spark-streaming`, `spark-aggregations` | Kafka → TimescaleDB raw and aggregates ([stream processing](architecture/stream-processing.md)) | none | `services/spark_jobs/` |
| `spark-master`, `spark-worker` | Standalone Spark cluster | 7077, 8083 (master UI) | `services/spark_jobs/Dockerfile.worker` |
| `alert-service-detector` | Evaluates alert rules ([alerting](architecture/alerting.md)) | none | `services/alert_service/` |
| `timescaledb` | System of record, schema-per-tenant ([data and storage](architecture/data-and-storage.md)) | `DB_PORT` | `infrastructure/timescaledb/` |
| `kafka`, `zookeeper` | Sensor-data topic | 9092, 29092; 2181 | `docker-compose.yml` |
| `redis` | Gateway cache, alert-worker snapshot, capability store | `REDIS_PORT` | `docker-compose.yml` |
| `minio` | Model artifacts, Spark checkpoints, backups, cold layer | 9000 (API), 9001 (console) | `infrastructure/minio/minio-init.sh` |
| `mlflow` | Tracking server and model registry | 5000 | `infrastructure/mlflow/` |
| `prometheus`, `grafana`, `loki`, `promtail`, exporters | Metrics, dashboards, logs | [monitoring](operations/monitoring.md#components) | `infrastructure/` |
| `notebook-*` (profile `notebooks`) | Notebook hub and its mediators | none | [notebooks](architecture/notebooks.md#components) |
| `db-backup` (profile `backup`) | Database backups | none | [backup](operations/backup-and-recovery.md) |
| `cold-export` (profile `cold-export`) | Exports raw history to the cold layer | none | `services/cold_export/` |

## API

Swagger UI: `http://localhost:8000/docs`. Login and token examples:
[authentication](operations/authentication.md#endpoints). Alert-rule example:
[alerting](architecture/alerting.md#rule-fields).

## Tests

| Task | Command |
|---|---|
| A service's unit tests | `pip install ./libs/industryflow_contracts -r services/<service>/requirements-dev.txt && pytest services/<service>/tests` |
| Python client | `pip install -e 'clients/python[dev]' && pytest clients/python/tests` |
| Config guards | `pip install pytest pyyaml ./libs/industryflow_contracts && pytest tests/config_guards` |
| Load test (running stack, device cert, provisioned sensors) | [capacity](operations/capacity.md#reproduce) |

CI workflows and required checks: [CI](operations/ci.md).

## Performance settings

| Name | Default | Meaning |
|---|---|---|
| `SPARK_STREAMING_CORES_MAX` / `SPARK_AGG_CORES_MAX` | 1 / 3 | Cores per Spark job |
| `SPARK_SHUFFLE_PARTITIONS` | 8 | Shuffle partitions of the measurement job; fixed once its checkpoint exists |
| `SPARK_AGG_STATE_PARTITIONS` | 2 | Window-state partitions per aggregation query; a change starts fresh aggregation checkpoints |
| `SPARK_DRIVER_MEMORY` / `SPARK_EXECUTOR_MEMORY` | `512m` / `512m` | Spark heap per job |
| `SPARK_WORKER_MEMORY` | `1g` | Memory the worker gives both executors together |
| `SPARK_DAEMON_MEMORY` | `256m` | Heap of the Spark master and worker daemons |
| `SPARK_*_MEM_LIMIT` | master `384m`, worker `2304m`, each job `1g` | Container memory ceilings; raise with the heaps |
| `KAFKA_HEAP_OPTS` / `KAFKA_MEM_LIMIT` | `-Xms512m -Xmx512m` / `1g` | Broker heap; container memory ceiling |
| `MLFLOW_WORKERS` / `MLFLOW_MEM_LIMIT` | 1 / `768m` | Tracking-server processes; container memory ceiling |
| `SPARK_CPU_SHARES` | 256 | CPU weight of the Spark containers under contention (others 1024); the ingest path goes first |
| `SPARK_WORKER_REPLICAS` | 1 | Spark workers |

Compression and retention: [data and storage](architecture/data-and-storage.md#tables). Measured
capacity: [capacity](operations/capacity.md). HA gaps: [production readiness](operations/production-readiness.md),
[high availability](operations/high-availability.md).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A service does not start | Misconfiguration or disk full | `docker compose logs <service>`; `docker system df` |
| `ingestion-edge` restarts, `device-crl-refresh` exits | Device CA not initialised | `scripts/device-ca.sh init` (step 2) |
| Database connection errors | `timescaledb` down or unreachable | `docker logs industryflow-timescaledb`; `docker exec industryflow-timescaledb psql -U postgres -c "SELECT 1"` |
| No messages in Kafka | Producer not reaching ingestion, or no topic | `docker exec industryflow-kafka kafka-topics --list --bootstrap-server localhost:9092`; `kafka-consumer-groups --list` likewise |
| Device rejected at the edge | Certificate or CRL | [device mTLS](operations/device-mtls.md#troubleshooting) |
| Metrics or logs missing | Monitoring stack | [monitoring](operations/monitoring.md#troubleshooting) |
