<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Capacity

Measured ingest capacity of the Compose stack on one host, how to reproduce it, and how to raise it.

## Measured on one node: 8 GB RAM, 4 cores

Single-node deployment: the whole platform runs on the host during the test, with the default
[performance settings](../getting-started.md#performance-settings).

| Running on the node | Containers |
|---|---|
| Ingest path | `ingestion-edge`, `ingestion-service`, `kafka`, `zookeeper` |
| Stream processing | `spark-master`, `spark-worker`, `spark-streaming`, `spark-aggregations` |
| Storage | `timescaledb`, `minio`, `redis` |
| Platform | `frontend`, `api-gateway`, `alert-service-detector`, `ml-service-api`, `mlflow` |
| Notebooks profile | `notebook-hub`, `notebook-sso`, `notebook-sql-proxy`, `notebook-tracking-gateway`, `notebook-cold-broker` |
| Monitoring | `prometheus`, `grafana`, `loki`, `promtail`, `cadvisor`, `node-exporter`, `kafka-exporter`, `postgres-exporter`, `redis-exporter` |
| Other workload | 3 containers unrelated to the platform |

Load: one reading per request through the mTLS edge, one tenant, 11 sensors.

| Rate (readings/s) | Duration | Errors | Ingest p95 | Landed | Host CPU | Verdict |
|---|---|---|---|---|---|---|
| 350 | 120 s | 0 | 31 ms | all | 75% | Pass |
| 500 | 120 s | 0 | 35 ms | all | 82% | Pass |
| 500 | 56 min | 0 | 33–176 ms per minute; 3 minutes 0.6–5.4 s | all | 82% | Pass on errors and landed |
| 750 | 120 s | 0 | 165 ms | all | 95% | Pass |
| 1 000 (offered) | 120 s | 0 | 6.4 s | all | 97% | 876/s achieved |

| Pass criterion | Value |
|---|---|
| Errors | 0 non-2xx |
| Landed | every accepted reading in `sensor_measurements` |
| Ingest p95 | < 500 ms at the device |
| Containers | no restart, no out-of-memory kill |

**Sustained capacity of one node: 500 readings/s (56-minute soak); 750 readings/s passes for 120 s.
Ceiling: about 875 readings/s, bound by CPU.**

| Rate | Equivalent |
|---|---|
| 500 readings/s | 1.8 million per hour, 43.2 million per day |
| 500 sensors | at 1 Hz |
| 5 000 sensors | at 0.1 Hz |

## Where the CPU goes at 750 readings/s

| Container | Cores |
|---|---|
| `ingestion-service` | 0.89 |
| `alert-service-detector` | 0.65 |
| `spark-worker` (executors) | 0.62 |
| `ingestion-edge` (TLS) | 0.24 |
| `timescaledb`, `kafka`, `spark-streaming`, `spark-aggregations`, `minio` | 0.12–0.13 each |

Spark runs at a lower CPU weight than the ingest path (`SPARK_CPU_SHARES`). Under contention the ingest
path keeps its latency, and Kafka holds what Spark has not processed yet.

## Raising capacity

| Option | Effect |
|---|---|
| More cores on the host | Raises the CPU ceiling; the ingest path and Spark scale with cores |
| More memory | Removes swap use under sustained load |
| Kubernetes ([Helm chart](../../deploy/helm/industryflow/README.md)) | Ingestion, edge and Spark scale out ([high availability](high-availability.md)) |

## Reproduce

Tools in `tests/performance/`. Run the generator on a separate machine, on a wired network; on the Docker
host run only the sampler.

| Tool | Runs on | Needs |
|---|---|---|
| `loadgen.py` | Load machine | Python 3, `httpx`, a device certificate |
| `sample_host.sh` | Docker host | cgroup v2, Docker's systemd cgroup driver |
| `analyze.py` | Anywhere | Python 3, the sampler's log |

| Step | Command |
|---|---|
| Device certificate | `scripts/device-ca.sh issue …` ([device mTLS](device-mtls.md)) |
| Sensor map | JSON object `name -> sensor UUID` of sensors in the device's tenant |
| Start the sampler (Docker host) | `bash tests/performance/sample_host.sh /tmp/sample.log &` |
| Load, one process per 125 readings/s | `python3 tests/performance/loadgen.py --url https://<host>:8443/ingest --cert <crt> --key <key> --sensor-map <map.json> --equipment-id <uuid> --site-id <site> --rate 125 --duration 120 --run-start <iso-time> --csv out-K.csv --workers 2 --worker K` (K = 0, 1 for 250 readings/s) |
| Count landed rows | `SELECT count(*) FROM "<tenant-schema>".sensor_measurements WHERE time >= '<iso-time>' AND time < '<iso-time>'::timestamptz + '<sent> microseconds'::interval` |
| Stop the sampler | `kill %1` in the sampler's shell |
| Summarize host and containers | `python3 tests/performance/analyze.py /tmp/sample.log <start-epoch> <end-epoch>` |

Reading `n` is stamped `--run-start` plus `n` microseconds, so the landed count for any prefix of a run
is exact. Server-side latency is `http_request_duration_seconds{handler="/ingest"}` in Prometheus.

| Symptom | Cause | Fix |
|---|---|---|
| Client p95 in seconds and achieved rate below the offered rate; server `/ingest` p95 low | A generator process above 125 readings/s lags its own event loop | One `loadgen.py` process per 125 readings/s |
| Timeouts in bursts; Docker host CPU drops at the same time | Load machine's network dropped out (Wi-Fi) | Wired network |
| Edge latency rises with no server-side change | `docker stats` polling on the Docker host starves the ingest edge | Sample with `sample_host.sh` only |
| A container missing from `analyze.py` output | Started after the sampler | Restart the sampler after the stack is up |
