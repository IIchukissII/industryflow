<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Getting started

Running the IndustryFlow `docker compose` stack locally: setup, first tenant and admin, endpoints,
API examples and troubleshooting. Concrete values live in [`docker-compose.yml`](../docker-compose.yml)
and [`.env.example`](../.env.example) ([ADR-0000](../ADR/ADR-0000-decision-records-and-source-of-truth.md));
this page names them, it does not copy them.

## Prerequisites

| Requirement | Used by |
|-------------|---------|
| Docker with Compose v2 | the stack |
| `openssl` on the host | `scripts/device-ca.sh`, `scripts/gen-internal-ca.sh` |
| Python 3 with `requests` and `pandas` | the reference data producer |
| 8 GB+ RAM | the stack |

## 1. Configure

```bash
git clone https://github.com/IIchukissII/industryflow
cd industryflow
cp .env.example .env
```

| Variable group | Action |
|----------------|--------|
| Every `changeme_*` value (DB roles, MinIO principals, `JWT_SECRET_KEY`, `INTERNAL_SERVICE_TOKEN`, `GRAFANA_ADMIN_PASSWORD`, …) | Replace with generated secrets |
| `FRONTEND_HTTPS_PORT`, `FRONTEND_PORT`, `INGESTION_MTLS_PORT` | Published ports of the HTTPS edge, its HTTP redirect and the device mTLS edge |
| `CERT_EXTRA_SAN`, `TLS_CERT_DIR` | Edge certificate ([TLS](operations/tls.md)) |
| `DEVICE_CA_DIR`, `DEVICE_CA_UID`, `DEVICE_CA_GID` | Device CA location and owner ([device mTLS](operations/device-mtls.md)) |
| `ADMIN_USER_<n>_*` | First users (step 4) |
| `DOCKER_GID`, `JUPYTERHUB_CRYPT_KEY`, `NOTEBOOK_SQL_PROXY_*` | Notebooks profile only |

## 2. Initialise the device CA

The ingestion edge and `device-crl-refresh` do not start without it.

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
# Prints the company_id and schema
scripts/create-tenant.sh "Your Company"

# In .env: ADMIN_USER_1_EMAIL, ADMIN_USER_1_PASSWORD, ADMIN_USER_1_COMPANY_ID=<company_id>
# The seeder runs in a one-off api-gateway container; -e NAME copies the variable from this
# shell, so the password is never an argument.
set -a; . ./.env; set +a
docker compose run --rm --no-deps -v ./scripts:/scripts:ro -e PYTHONPATH=/app \
  -e ADMIN_USER_1_EMAIL -e ADMIN_USER_1_PASSWORD -e ADMIN_USER_1_COMPANY_ID -e ADMIN_USER_1_ROLE \
  -e ADMIN_USER_1_SUPERUSER \
  api-gateway python /scripts/seed_users.py
```

The seeded user is a tenant admin; `ADMIN_USER_1_SUPERUSER=true` makes it a cross-tenant superuser.
Further users are created by an admin ([user management](operations/user-management.md)).

## 5. Endpoints

Published on every interface:

| Endpoint | URL | Auth |
|----------|-----|------|
| Frontend (HTTPS edge) | `https://localhost:<FRONTEND_HTTPS_PORT>` | platform login |
| HTTP → HTTPS redirect | `http://localhost:<FRONTEND_PORT>` | — |
| Notebook hub (through the edge) | `https://localhost:<FRONTEND_HTTPS_PORT>/jupyter/` | platform SSO |
| Device ingestion (mTLS edge) | `https://localhost:<INGESTION_MTLS_PORT>/ingest` | device certificate |

Bound to `127.0.0.1` only ([ADR-0004](../ADR/ADR-0004-api-authentication-sessions-and-transport.md),
[ADR-0013](../ADR/ADR-0013-experiment-tracking-and-model-registry-multitenancy.md)):

| Service | Host port |
|---------|-----------|
| API gateway | `API_GATEWAY_PORT` |
| ML service | `ML_SERVICE_PORT` |
| TimescaleDB | `DB_PORT` |
| Redis | `REDIS_PORT` |
| Kafka | 9092 (host listener), 29092 |
| Zookeeper | 2181 |
| MinIO API / console | 9000 / 9001 |
| MLflow | 5000 |
| Spark master / UI | 7077 / 8083 |
| Prometheus | `PROMETHEUS_PORT` |
| Grafana | `GRAFANA_PORT` (`GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`) |
| Loki | 3100 |
| cAdvisor, node, postgres, redis, kafka exporters | 8080, 9100, 9187, 9121, 9308 |

From another machine, reach a loopback-bound service through an SSH tunnel
(`ssh -L 5000:localhost:5000 <host>`). The browser uses the API through the edge
(`https://<host>:<FRONTEND_HTTPS_PORT>/api/…`).

### Reaching the edge from another machine

| Name | Requirement |
|------|-------------|
| `https://industryflow.local:<FRONTEND_HTTPS_PORT>` | mDNS on host and client, or a hosts entry `<host-ip> industryflow.local` |
| `https://<host-ip>:<FRONTEND_HTTPS_PORT>` | the IP is in the certificate SAN |

The self-signed default certificate covers `industryflow.local`, `industryflow`, `localhost` and
`127.0.0.1`, plus `CERT_EXTRA_SAN`. `scripts/gen-internal-ca.sh` adds the host's primary IP. See
[TLS](operations/tls.md).

## 6. Notebooks (optional profile)

Per-user JupyterHub notebooks ([ADR-0011](../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md),
[ADR-0018](../ADR/ADR-0018-notebook-hub-spawner-portability.md)). The hub spawns per-user containers
through the Docker socket, so set `DOCKER_GID` (`getent group docker | cut -d: -f3`) and
`JUPYTERHUB_CRYPT_KEY` in `.env`.

```bash
docker compose --profile notebooks build
docker compose --profile notebooks up -d
```

Open `https://<host>:<FRONTEND_HTTPS_PORT>/jupyter/` after logging in to the platform. Kernels hold
capability handles, never a database password ([ADR-0012](../ADR/ADR-0012-notebook-credential-delivery.md),
[ADR-0015](../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md)). Details:
[notebooks](architecture/notebooks.md), [Python client](../clients/python/README.md).

## 7. Stream test data

The reference producer in `extensions/tep-reference` streams the Tennessee-Eastman example dataset
over device mTLS ([device mTLS](operations/device-mtls.md)).

```bash
scripts/device-ca.sh issue <company_id> tep-producer

# Terminal 1: stream
DEVICE_CERT=deploy/device-ca/devices/<company_id>/tep-producer/current.chain.crt \
DEVICE_KEY=deploy/device-ca/devices/<company_id>/tep-producer/current.key \
  python3 extensions/tep-reference/producer/stream_tep_data.py

# Terminal 2: watch the streaming job
docker logs -f industryflow-spark-streaming
```

| Producer variable | Default | Meaning |
|-------------------|---------|---------|
| `INGESTION_URL` | `https://localhost:8443/ingest` | mTLS edge endpoint |
| `DEVICE_CERT`, `DEVICE_KEY` | — (required) | device certificate chain and key |
| `INGEST_CA` | empty (server cert not verified) | CA bundle for the edge's server certificate |
| `EQUIPMENT_ID`, `SITE_ID` | see the script | identifiers sent with each reading |

## Components

| Component | Compose service | Role | Docs |
|-----------|-----------------|------|------|
| Frontend / TLS edge | `frontend` | React app; same-origin reverse proxy to the APIs and the hub | [TLS](operations/tls.md) |
| API gateway | `api-gateway` | Auth, tenant data, alert rules and history, websocket | [API](api/README.md) |
| ML service | `ml-service-api` | Models, feature configs, inference, drift evaluation | [ML](architecture/ml-and-features.md) |
| Ingestion edge | `ingestion-edge` | Device mTLS termination, CRL check | [device mTLS](operations/device-mtls.md) |
| Ingestion service | `ingestion-service` | Produces readings to Kafka; reachable only from the edge | [stream processing](architecture/stream-processing.md) |
| Device CRL refresh | `device-crl-refresh` | Re-signs the device CRL | [device mTLS](operations/device-mtls.md) |
| Spark streaming | `spark-streaming` | Kafka → TimescaleDB measurements | [stream processing](architecture/stream-processing.md) |
| Spark aggregations | `spark-aggregations` | 1 min / 5 min / 1 hour aggregates | [stream processing](architecture/stream-processing.md) |
| Alert detector | `alert-service-detector` | Consumes Kafka, evaluates rules, writes alerts | [alerting](architecture/alerting.md) |
| TimescaleDB | `timescaledb` | System of record, schema-per-tenant | [data & storage](architecture/data-and-storage.md) |
| Kafka / Zookeeper | `kafka`, `zookeeper` | Sensor-data topic | [stream processing](architecture/stream-processing.md) |
| Redis | `redis` | Gateway cache, alert-worker latest-value snapshot, notebook capability store | — |
| MinIO | `minio` | Model artifacts, Spark checkpoints, backups, cold layer | [data & storage](architecture/data-and-storage.md) |
| MLflow | `mlflow` | Tracking server and model registry | [ML](architecture/ml-and-features.md) |
| Monitoring | `prometheus`, `grafana`, `loki`, `promtail`, exporters | Metrics, dashboards, logs | [monitoring](operations/monitoring.md) |
| Backup | `db-backup` (profile `backup`) | Database backups | [backup](operations/backup-and-recovery.md) |
| Cold export | `cold-export` (profile `cold-export`) | Exports history to the cold layer | [ADR-0025](../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |

## API examples

Bearer flow for API clients. Browsers use httpOnly cookies + CSRF
([authentication](operations/authentication.md)). Full reference: `http://localhost:8000/docs`.

```bash
# Login: returns access_token and refresh_token
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=<email>&password=<password>'

# Latest measurements
curl http://localhost:8000/api/measurements/latest -H "Authorization: Bearer <token>"

# One sensor over a window
curl "http://localhost:8000/api/measurements/<sensor_id>?start=<iso8601>&end=<iso8601>&order=asc" \
  -H "Authorization: Bearer <token>"

# Alert rules
curl http://localhost:8000/api/alert-rules -H "Authorization: Bearer <token>"

curl -X POST http://localhost:8000/api/alert-rules \
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

| Alert-rule field | Values |
|------------------|--------|
| `detection_type` | `threshold`, `ml`, `statistical` (`ml` and `statistical` require `model_id`) |
| `condition` | `greater_than`, `less_than`, `equals`, `not_equals`, `between`, `outside_range` |
| `severity` | `low`, `medium`, `high`, `critical` |

## Tests

| Suite | Command |
|-------|---------|
| A service's unit tests | `pip install ./libs/industryflow_contracts -r services/<service>/requirements-dev.txt && pytest services/<service>/tests` |
| Python client | `pip install -e 'clients/python[dev]' && pytest clients/python/tests` |
| Config guards | `pip install pytest pyyaml ./libs/industryflow_contracts && pytest tests/config_guards` |
| Load test (running stack, device cert) | `cd tests/performance && python3 performance_test.py` |

The CI matrix and required checks: [CI](operations/ci.md).

## Troubleshooting

| Symptom | Check |
|---------|-------|
| A service does not start | `docker compose logs <service>`; `docker system df` for disk space |
| `ingestion-edge` restarts, `device-crl-refresh` exits | `scripts/device-ca.sh init` was not run (step 2) |
| Database connection errors | `docker logs industryflow-timescaledb`; `docker exec industryflow-timescaledb psql -U postgres -c "SELECT 1"` |
| No messages in Kafka | `docker exec industryflow-kafka kafka-topics --list --bootstrap-server localhost:9092`; `docker exec industryflow-kafka kafka-consumer-groups --list --bootstrap-server localhost:9092` |
| Device rejected at the edge | [device mTLS](operations/device-mtls.md) |
| Monitoring | [monitoring troubleshooting](operations/monitoring.md#troubleshooting) |

## Performance settings

| Setting | Location |
|---------|----------|
| Compression and retention policies | tenant hypertables, `infrastructure/timescaledb/init-scripts/` |
| Spark cores per job | `SPARK_STREAMING_CORES_MAX`, `SPARK_AGG_CORES_MAX` (`docker-compose.yml`) |
| Spark shuffle partitions | `SPARK_SHUFFLE_PARTITIONS` (`docker-compose.yml`) |
| Spark memory | `SPARK_DRIVER_MEMORY`, `SPARK_EXECUTOR_MEMORY` (`.env`) |
| Spark workers | `SPARK_WORKER_REPLICAS` (`.env`) |

Capacity and HA gaps: [production readiness](operations/production-readiness.md),
[high availability](operations/high-availability.md).
