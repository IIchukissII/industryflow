<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Capacity

Measured ingest capacity and memory need of the Compose stack on one host, how to reproduce them, and how to raise capacity.

## Measured on one node: 8 GB RAM, 4 cores

Single-node deployment: the whole platform runs on the host during the test, with the default
performance settings (ADR-0009 dec 6).

| Running on the node | Containers |
|---|---|
| Ingest path | `ingestion-edge`, `ingestion-service`, `kafka`, `zookeeper` |
| Stream processing | `spark-master`, `spark-worker`, `spark-streaming`, `spark-aggregations` (the 2026-10-18 run; local mode since #743) |
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

## Memory

The platform's need per ingest tier, the source of `deploy/host-memory.toml` (manual: Getting started, Memory budget).
Measured 2026-11-25 on the 8 GB node (7 765 MiB, 4 095 MiB swap), every container under a 4 GiB limit,
so what is measured is the need and not a limit.

| Window | Load | Device p95 | Errors |
|---|---|---|---|
| Rest | none | | |
| 250/s | 300 s | 26–27 ms | 0 beyond the tenant's own cap |
| 500/s | 300 s | 75–250 ms | 0 |
| End-to-end journeys | `scripts/box-e2e.sh` with 240/s beside it, 15 min | 27 ms | 0 beyond the tenant's own cap |
| Start | every service recreated, sampled each second, 5 min | | |

| Tier | Resident | Cold | Windows |
|---|---|---|---|
| 250/s | 5 512 MiB | 2 861 MiB | rest, 250/s, journeys; start |
| 500/s | 5 462 MiB | 2 872 MiB | rest, 500/s, journeys; start |

| Part | Counted from cgroup v2 | Must sit in |
|---|---|---|
| Resident | peak of `anon` + `shmem` + `kernel` + `file_mapped` beyond `shmem` | Memory |
| Cold | peak of resident + `memory.swap.current` over the windows and the start, less the resident peak | Swap |

A start touches every page once and is over in seconds: its peak counts toward what a service may hold
(memory and swap), not toward what it keeps in memory. Sampled every 10 s, a start's peak is missed; the
alert worker was stopped at its start (out of memory) until the start window was measured.

Load changes the need by under 2% between rest and 500/s: the JVM heaps and the Python processes are
fixed. The journeys raise the idle services' resident need (`ml-service-api` 34 → 405 MiB, `mlflow`
14 → 83 MiB), so a tier always includes them.

| Largest needs (500/s) | Resident | Cold |
|---|---|---|
| `spark-aggregations` | 1 275 MiB | 444 MiB |
| `spark-streaming` | 1 239 MiB | 418 MiB |
| `ml-service-api` | 405 MiB | 557 MiB |
| `grafana` | 244 MiB | 233 MiB |
| `minio` | 364 MiB | 17 MiB |
| `kafka` | 290 MiB | 265 MiB |
| `mlflow` | 83 MiB | 360 MiB |

| Beside the containers | Measured |
|---|---|
| Docker, containerd, the kernel outside every cgroup | 605 MiB |
| A database backup (`db-backup`) | 85 MiB |

### Measure a tier

| Step | Command |
|---|---|
| Loose limits (keeps the heaps) | `python3 scripts/host-memory.py`, then set every `*_MEM_LIMIT` and `*_MEMSWAP_LIMIT` in the block to `4096m`; `docker compose` with every profile the host runs (`--profile notebooks --profile ops-agent`) `up -d`; check each container's `HostConfig.Memory` |
| Start the sampler (Docker host) | `bash tests/performance/sample_memory.sh /tmp/memory.log &` |
| Windows | rest 5 min; the tier's rate for 5 min ([reproduce](#reproduce)); `scripts/box-e2e.sh` with ingest beside it |
| Start | a second sampler at 1 s (`sample_memory.sh <log> 1`), then `up -d --force-recreate` of every long-running service; 5 min |
| The tier's lines | `python3 tests/performance/memory_need.py <logs joined> <start>:<end> … --start <start>:<end> --tier <rate>/s --readings-per-second <rate> --host-mib <MemTotal MiB>` |
| Restore | `python3 scripts/host-memory.py`; the same `up -d` |

## Raising capacity

| Option | Effect |
|---|---|
| More cores on the host | Raises the CPU ceiling; the ingest path and Spark scale with cores |
| More memory | A notebook share ([memory](#memory)); the platform's own need does not grow with the host |
| Kubernetes (ADR-0009) | Ingestion, edge and Spark scale out (ADR-0009) |

## Reproduce

Tools in `tests/performance/`. Run the generator on a separate machine, on a wired network; on the Docker
host run only the sampler.

| Tool | Runs on | Needs |
|---|---|---|
| `loadgen.py` | Load machine | Python 3, `httpx`, `pip install ./libs/industryflow_contracts`, a device certificate and the edge's CA |
| `sample_host.sh` | Docker host | cgroup v2, Docker's systemd cgroup driver |
| `analyze.py` | Anywhere | Python 3, the sampler's log |

| Step | Command |
|---|---|
| Device certificate | `scripts/device-ca.sh issue …` (ADR-0007) |
| Sensor map | JSON object `name -> sensor UUID` of sensors in the device's tenant |
| Start the sampler (Docker host) | `bash tests/performance/sample_host.sh /tmp/sample.log &` |
| Load, one process per 125 readings/s | `python3 tests/performance/loadgen.py --url https://<host>:8443/ingest --cert <crt> --key <key> --ca <edge-ca.crt> --sensor-map <map.json> --equipment-id <uuid> --site-id <site> --rate 125 --duration 120 --run-start <iso-time> --csv out-K.csv --workers 2 --worker K` (K = 0, 1 for 250 readings/s) |
| Count landed rows | `SELECT count(*) FROM "<tenant-schema>".sensor_measurements WHERE time >= '<iso-time>' AND time < '<iso-time>'::timestamptz + '<sent> microseconds'::interval` |
| Stop the sampler | `kill %1` in the sampler's shell |
| Summarize host and containers | `python3 tests/performance/analyze.py /tmp/sample.log <start-epoch> <end-epoch>` |

Reading `n` is stamped `--run-start` plus `n` microseconds, so the landed count for any prefix of a run
is exact. Server-side latency is `http_request_duration_seconds{handler="/ingest"}` in Prometheus.

Symptoms, causes and fixes of a load run are rows of the infrastructure agent's runbook data,
`services/ops_agent/runbook.toml`, following ADR-0009 dec 6.
