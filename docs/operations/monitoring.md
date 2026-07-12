<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow Monitoring & Observability

Complete guide to the monitoring, metrics, and logging infrastructure for IndustryFlow.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Components](#components)
- [Access & Credentials](#access--credentials)
- [Dashboards](#dashboards)
- [Metrics](#metrics)
- [Logs](#logs)
- [Alerts](#alerts)
- [Troubleshooting](#troubleshooting)

---

## Overview

IndustryFlow uses a comprehensive monitoring stack built on industry-standard tools:

- **Prometheus** - Time-series metrics collection and storage
- **Grafana** - Visualization and dashboarding
- **Loki** - Log aggregation and querying
- **Promtail** - Log collection agent
- **Multiple Exporters** - Specialized metrics collectors

### Key Features

- Real-time metrics from all services
- Centralized log aggregation from 23+ containers
- Custom dashboards for system and application monitoring
- 7-day log retention
- 30-day metrics retention
- Automated service discovery

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Grafana (Port 3001)                     │
│                  Visualization & Dashboards                  │
└────────────────┬─────────────────────┬──────────────────────┘
                 │                     │
        ┌────────▼────────┐   ┌───────▼────────┐
        │   Prometheus    │   │      Loki      │
        │   (Port 9090)   │   │   (Port 3100)  │
        │  Metrics Store  │   │   Log Store    │
        └────────┬────────┘   └───────┬────────┘
                 │                     │
    ┌────────────┼─────────────┐      │
    │            │             │      │
┌───▼────┐ ┌────▼────┐ ┌──────▼──┐ ┌─▼────────┐
│cAdvisor│ │  Node   │ │Exporters│ │ Promtail │
│        │ │Exporter │ │(DB,etc) │ │          │
└───┬────┘ └────┬────┘ └──────┬──┘ └─┬────────┘
    │           │             │      │
    └───────────┴─────────────┴──────┴──────────┐
                                                 │
              ┌──────────────────────────────────▼────┐
              │         Docker Containers              │
              │  (API, Services, Databases, Kafka...)  │
              └────────────────────────────────────────┘
```

---

## Components

### Prometheus

**Purpose**: Metrics collection, storage, and querying engine

**Configuration**: `/infrastructure/prometheus/prometheus.yml`

**Key Settings**:
- Scrape interval: 15 seconds
- Evaluation interval: 15 seconds
- Retention: 30 days
- Storage: `/prometheus-data` volume

**Scraped Targets** (13 total):
1. **prometheus** (self-monitoring)
2. **cadvisor** - Container metrics
3. **node-exporter** - System metrics
4. **postgres** - Database metrics
5. **redis** - Cache metrics
6. **api-gateway** - API service metrics
7. **alert-service** - Alert service metrics
8. **ml-service** - ML service metrics
9. **ingestion-service** - Ingestion service metrics
10. **kafka** - Kafka cluster metrics
11. **spark-driver** - Spark streaming driver metrics
12. **spark-executors** - Spark aggregations/executor metrics
13. **spark-master** - Spark master metrics

**Access**: http://localhost:9090

---

### Grafana

**Purpose**: Metrics visualization and dashboard management

**Configuration**:
- Datasources: `/infrastructure/grafana/provisioning/datasources.yml`
- Dashboards: `/infrastructure/grafana/provisioning/dashboards.yml`

**Port**: 3001 (configurable via `GRAFANA_PORT` in `.env`)

**Default Credentials**:
- Username: `admin`
- Password: Check `.env` file (`GRAFANA_ADMIN_PASSWORD`)

**Configured Datasources**:
1. **Prometheus** (default) - Metrics
2. **Loki** - Logs
3. **TimescaleDB** - Time-series data

**Volume**: `/grafana-data` for persistent storage

---

### Loki

**Purpose**: Log aggregation and querying system

**Configuration**: `/infrastructure/loki/loki-config.yml`

**Port**: 3100

**Key Settings**:
- Retention period: 7 days (168 hours)
- Ingestion rate: 16 MB/s
- Max streams per user: 10,000
- Compaction interval: 10 minutes

**Storage**: `/loki-data` volume

**Log Sources**:
- All Docker containers (via Promtail)
- System logs from `/var/log`

---

### Promtail

**Purpose**: Log collection and forwarding to Loki

**Configuration**: `/infrastructure/promtail/promtail-config.yml`

**Key Features**:
- Automatic Docker container discovery
- Label extraction from container metadata
- JSON log parsing
- Real-time log streaming

**Labels Applied**:
- `job` - "docker" for containers, "varlogs" for system
- `container` - Container name
- `service` - Compose service name
- `project` - Compose project name
- `environment` - "production"
- `cluster` - "industryflow"

---

### Exporters

#### cAdvisor (Container Advisor)

**Port**: 8080 (internal)

**Metrics**:
- Container CPU usage
- Container memory usage
- Network I/O per container
- Filesystem usage

**Configuration**:
- Housekeeping interval: 30 seconds
- Docker-only mode enabled
- Disk metrics disabled (performance)

#### Node Exporter

**Port**: 9100

**Metrics**:
- System CPU usage
- System memory usage
- Disk I/O and usage
- Network statistics
- Load averages

#### PostgreSQL Exporter

**Port**: 9187

**Metrics**:
- Database sizes
- Connection counts
- Query performance
- Transaction rates
- Table statistics

**Configuration**:
- Connects to: `timescaledb:5432`
- Database: `industryflow`
- Credentials: Uses `DB_USER` and `DB_PASSWORD` from `.env`

#### Redis Exporter

**Port**: 9121

**Metrics**:
- Memory usage
- Connected clients
- Hit/miss rates
- Key statistics
- Command statistics

#### Kafka Exporter

**Port**: 9308

**Metrics**:
- Broker status
- Topic metrics
- Partition counts
- Consumer lag
- Message rates

---

## Access & Credentials

### Grafana Access

**URL**: http://localhost:3001

**Login**:
```
Username: admin
Password: <from .env GRAFANA_ADMIN_PASSWORD>
```

**Default password location**: `.env` file at project root

### Prometheus Access

**URL**: http://localhost:9090

**No authentication required** (internal use)

### Loki Access

**URL**: http://localhost:3100

**API Endpoints**:
- Metrics: `http://localhost:3100/metrics`
- Ready check: `http://localhost:3100/ready`
- Query API: `http://localhost:3100/loki/api/v1/query`

---

## Dashboards

### 1. Complete System Infrastructure

**UID**: `complete-system`

**URL**: http://localhost:3001/d/complete-system/complete-system-infrastructure

**Panels**:
- **Service Health Status** - UP/DOWN for all monitored services
- **System Resources** - CPU, Memory, Disk usage gauges
- **Container CPU Usage** - Time series of container CPU
- **Container Memory Usage** - Time series of container memory
- **Network I/O** - Container network traffic
- **Database Metrics**:
  - Active connections
  - Database sizes
  - Query performance
- **Redis Metrics**:
  - Memory usage
  - Cache hit rates
  - Connected clients
- **Kafka Metrics**:
  - Topic offsets
  - Consumer lag
  - Broker status

**Refresh Rate**: 10 seconds (configurable)

---

### 2. Container Logs Dashboard

**UID**: `container_logs`

**URL**: http://localhost:3001/d/container_logs/container-logs-dashboard

**Panels**:
- **Log Rate by Service** - Bar chart showing log volume
- **Active Containers** - Count of containers logging
- **Current Log Rate** - Real-time logs/second
- **Top 10 Services** - Services with highest log volume
- **Container Logs Viewer** - Full log explorer with:
  - Multi-container selection
  - Service filter
  - Text search
  - Label display
  - Time range selection

**Features**:
- Multi-select container filter
- Regex search support
- Real-time log streaming
- Label visibility for each log line
- Auto-refresh every 10 seconds

**Usage Examples**:

Filter specific containers:
1. Click "Container" dropdown
2. Uncheck "All"
3. Select desired containers (e.g., kafka, api-gateway)

Search for errors:
1. Enter "error" in Search box
2. Or use regex: `ERROR|FATAL|CRITICAL`

Filter by service:
1. Use "Service" dropdown
2. Select service (e.g., "alert-service-api")

---

## Runbook: the stateful-feature kill-switch

**What it is.** Some feature transforms (`statistical`, `rolling_stat`) are *stateful*: to compute a
feature they read a windowed baseline out of the Spark-materialized aggregate tables. Every
inference issues one such read per stateful feature, so a model carrying nine of them queries the
database nine times per prediction. When the database is degraded, that load is part of the problem.

The kill-switch (ADR-0024) neutralizes that whole class **live, without a redeploy**: the engine
fills each stateful feature's slot with its neutral value and does **not call the transform**, so
those queries stop entirely. The feature vector keeps its length and order, so every bound model
keeps serving — on partially degraded input.

**When to flip it.** The database is alive but overloaded, and inference is a meaningful part of the
load. This is not a fix for a *totally unreachable* database: there, the switch's own read fails
too, it holds `enabled`, and the transforms fall back to the same neutral value on their own. The
switch buys relief for an overloaded store, not resurrection of a dead one.

**Turn it off** (neutralize stateful features):

```sql
UPDATE public.platform_config
   SET value = 'false'::jsonb, updated_at = NOW(), updated_by = '<operator>'
 WHERE key = 'stateful_features_enabled';
```

**Turn it back on** when the substrate recovers — `value = 'true'::jsonb`.

The flag is read on the serving path with a short TTL cache, so a flip takes effect within seconds;
no restart, no redeploy. The service only ever **reads** it (it holds `SELECT` and nothing more), so
it cannot trip itself — the switch moves because a human moved it.

**Confirm it took effect.** Degraded serving is never silent: `/api/inference/predict` responses
carry `degraded: true` and list the `neutralized_features`, the service logs the neutralization, and
the counter rises:

```promql
# Feature slots being served neutral instead of computed — should be 0 in normal operation
rate(ml_stateful_features_neutralized_total[5m])
```

**Alert on it being left off.** The switch's cost is silent quality loss: scores keep coming, and
they look ordinary unless you check. A non-zero rate for longer than an incident should last is
worth paging on — it means models are scoring on neutralized input and somebody forgot to flip it
back.

## Metrics

### Available Metrics Categories

#### Application Metrics

From FastAPI services (API Gateway, Alert Service, ML Service):

```promql
# Request rate
rate(http_requests_total[5m])

# Request latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Error rate
rate(http_requests_total{status=~"5.."}[5m])
```

#### Container Metrics

From cAdvisor:

```promql
# Container CPU usage
rate(container_cpu_usage_seconds_total{name!=""}[5m])

# Container memory usage
container_memory_usage_bytes{name!=""}

# Container network I/O
rate(container_network_receive_bytes_total[5m])
```

#### System Metrics

From Node Exporter:

```promql
# CPU usage
100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# Memory usage
100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))

# Disk usage
100 - ((node_filesystem_avail_bytes / node_filesystem_size_bytes) * 100)
```

#### Database Metrics

From PostgreSQL Exporter:

```promql
# Active connections
pg_stat_database_numbackends{datname="industryflow"}

# Database size
pg_database_size_bytes{datname="industryflow"}

# Transaction rate
rate(pg_stat_database_xact_commit{datname="industryflow"}[5m])
```

#### Kafka Metrics

From Kafka Exporter:

```promql
# Consumer lag
kafka_consumergroup_lag

# Topic messages per second
rate(kafka_topic_partition_current_offset[5m])

# Broker count
count(kafka_brokers)
```

---

## Logs

### Query Syntax (LogQL)

**Basic Queries**:

```logql
# All Docker container logs
{job="docker"}

# Specific container
{container="industryflow-kafka"}

# Specific service
{service="api-gateway"}

# Multiple containers
{container=~"industryflow-(kafka|api-gateway)"}
```

**Filtering**:

```logql
# Contains text
{job="docker"} |= "error"

# Regex match
{job="docker"} |~ "ERROR|WARN|FATAL"

# Does not contain
{job="docker"} != "debug"

# JSON field extraction
{job="docker"} | json | level="error"
```

**Aggregations**:

```logql
# Count logs per service
sum by(service) (count_over_time({job="docker"}[5m]))

# Log rate per container
sum by(container) (rate({job="docker"}[1m]))

# Top 10 noisiest containers
topk(10, sum by(container) (count_over_time({job="docker"}[1h])))
```

### Accessing Logs

**Via Grafana**:
1. Navigate to Container Logs Dashboard
2. Use filters to narrow down logs
3. Click log lines to expand details

**Via Loki API**:

```bash
# Query last hour of logs
curl -G -s "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={job="docker"}' \
  --data-urlencode 'start=1h'

# Get available labels
curl -s "http://localhost:3100/loki/api/v1/labels"

# Get label values
curl -s "http://localhost:3100/loki/api/v1/label/container/values"
```

**Via LogCLI** (if installed):

```bash
# Tail logs
logcli query --tail '{container="industryflow-kafka"}'

# Query with time range
logcli query '{job="docker"} |= "error"' --since=1h
```

---

## Alerts

### Current Alert Configuration

The monitoring stack is configured for alerting but does not have active alert rules configured by default.

### Setting Up Alerts

**1. Create Alert Rules in Prometheus**:

Edit `/infrastructure/prometheus/prometheus.yml`:

```yaml
rule_files:
  - "alerts.yml"
```

Create `/infrastructure/prometheus/alerts.yml`:

```yaml
groups:
  - name: industryflow
    interval: 30s
    rules:
      - alert: HighCPUUsage
        expr: 100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High CPU usage detected"
          description: "CPU usage is above 80% for 5 minutes"

      - alert: ServiceDown
        expr: up == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Service {{ $labels.job }} is down"
```

**2. Configure Alert Manager** (optional):

Add to `docker-compose.yml` and update Prometheus config to point to it.

**3. Create Grafana Alerts**:

Grafana supports creating alerts directly from dashboard panels.

---

## Troubleshooting

### Prometheus Issues

**Prometheus not scraping targets**:

```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq

# Check Prometheus logs
docker logs industryflow-prometheus

# Verify config syntax
docker exec industryflow-prometheus promtool check config /etc/prometheus/prometheus.yml
```

**High memory usage**:

- Reduce retention time in `prometheus.yml`
- Reduce scrape frequency
- Limit cardinality of metrics

### Grafana Issues

**Dashboards not loading**:

```bash
# Check Grafana logs
docker logs industryflow-grafana

# Verify dashboard files permissions
ls -la infrastructure/grafana/provisioning/*.json

# Fix permissions if needed
chmod 644 infrastructure/grafana/provisioning/*.json
docker-compose restart grafana
```

**Datasource connection errors**:

- Verify service names in datasource URLs
- Check network connectivity: `docker exec industryflow-grafana ping prometheus`
- Verify credentials for TimescaleDB datasource

### Loki Issues

**No logs appearing**:

```bash
# Check Loki status
curl http://localhost:3100/ready

# Check available labels
curl http://localhost:3100/loki/api/v1/labels

# Check Promtail logs
docker logs industryflow-promtail

# Verify Promtail is sending logs
curl http://localhost:3100/loki/api/v1/label/container/values
```

**Parse errors in queries**:

- Ensure LogQL syntax is correct
- Use `{job="docker"}` not `{job=docker}`
- Time ranges need units: `[5m]` not `[5]`

### Promtail Issues

**Not collecting container logs**:

```bash
# Check Promtail logs
docker logs industryflow-promtail

# Verify Docker socket access
docker exec industryflow-promtail ls -la /var/run/docker.sock

# Check Promtail config
docker exec industryflow-promtail cat /etc/promtail/promtail-config.yml
```

**High memory usage**:

- Reduce batch size in Promtail config
- Increase batch timeout
- Limit number of streams

### Exporter Issues

**PostgreSQL Exporter not connecting**:

```bash
# Check exporter logs
docker logs industryflow-postgres-exporter

# Test database connection
docker exec industryflow-timescaledb psql -U postgres -c "SELECT 1"

# Verify credentials match .env
grep DB_PASSWORD .env
```

**Redis Exporter not working**:

```bash
# Check Redis connectivity
docker exec industryflow-redis redis-cli ping

# Check exporter status
curl http://localhost:9121/metrics | grep redis_up
```

### General Debugging

**Check all monitoring services status**:

```bash
docker ps --filter "name=prometheus\|grafana\|loki\|promtail" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

**Restart monitoring stack**:

```bash
docker-compose restart prometheus grafana loki promtail
```

**View resource usage**:

```bash
docker stats --filter "name=prometheus\|grafana\|loki\|promtail"
```

---

## Performance Tuning

### Prometheus

**Reduce cardinality**:
- Use recording rules for expensive queries
- Drop unnecessary labels with `metric_relabel_configs`

**Optimize storage**:
```yaml
# In prometheus.yml
storage:
  tsdb:
    retention.time: 15d  # Reduce from 30d if needed
    retention.size: 10GB # Add size limit
```

### Loki

**Reduce ingestion**:
```yaml
# In loki-config.yml
limits_config:
  ingestion_rate_mb: 8  # Reduce from 16
  per_stream_rate_limit: 4MB  # Reduce from 8MB
```

**Improve compaction**:
```yaml
compactor:
  compaction_interval: 5m  # Reduce from 10m
  retention_delete_worker_count: 200  # Increase from 150
```

### Grafana

**Optimize dashboard queries**:
- Use time-based aggregations
- Limit data points with `$__interval`
- Use query caching where possible

---

## Backup & Recovery

### Backing Up Metrics

**Prometheus**:
```bash
# Backup Prometheus data
docker run -v prometheus-data:/data -v $(pwd)/backup:/backup \
  busybox tar czf /backup/prometheus-backup.tar.gz /data
```

**Loki**:
```bash
# Backup Loki data
docker run -v loki-data:/data -v $(pwd)/backup:/backup \
  busybox tar czf /backup/loki-backup.tar.gz /data
```

### Backing Up Configuration

```bash
# Backup all monitoring configs
tar czf monitoring-config-backup.tar.gz \
  infrastructure/prometheus/ \
  infrastructure/loki/ \
  infrastructure/promtail/ \
  infrastructure/grafana/provisioning/
```

### Restoring

```bash
# Restore Prometheus data
docker run -v prometheus-data:/data -v $(pwd)/backup:/backup \
  busybox tar xzf /backup/prometheus-backup.tar.gz -C /

# Restart services
docker-compose restart prometheus grafana
```

---

## Best Practices

1. **Regular Health Checks**
   - Monitor Prometheus targets daily
   - Check Grafana dashboards weekly
   - Review log volume and retention

2. **Security**
   - Keep Grafana password secure (stored in .env)
   - Don't expose Prometheus/Loki ports externally
   - Use authentication for production deployments

3. **Performance**
   - Monitor monitoring stack resource usage
   - Tune retention based on disk space
   - Use recording rules for expensive queries

4. **Maintenance**
   - Backup configurations regularly
   - Update dashboard versions
   - Review and clean up unused metrics

5. **Documentation**
   - Document custom dashboards
   - Keep alert rule descriptions clear
   - Maintain runbooks for common issues

---

## Additional Resources

- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Loki Documentation](https://grafana.com/docs/loki/)
- [LogQL Query Language](https://grafana.com/docs/loki/latest/logql/)
- [PromQL Query Language](https://prometheus.io/docs/prometheus/latest/querying/basics/)
