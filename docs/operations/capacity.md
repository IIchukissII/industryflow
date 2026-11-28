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

The platform's need, the source of `deploy/host-memory.toml` (manual: Getting started, Resource budget).
Measured 2026-11-26 on the 8 GB node (7 765 MiB, 8 191 MiB swap), every container under a 4 GiB memory
limit and an 8 GiB memory-and-swap limit, so what is measured is the need and not a limit. Every
container's untouched pages were reclaimed once before the windows, so a page counts as resident only
when a window uses it.

| Window | Load | Device p95 | Resident (all services) | Errors |
|---|---|---|---|---|
| Rest | none | | 4 106 MiB | |
| 250/s | 300 s | 30 ms | 4 627 MiB | 0 beyond the tenant's own cap |
| 500/s | 300 s | 2–3 s, while the ingest path brings its pages back from swap | 4 816 MiB | 2.4% |
| Journeys | each profile's [session journey](#a-notebook-session), then `scripts/box-e2e.sh` (40 passed), 250/s beside them, 23 min | | 6 025 MiB | 0 beyond the tenant's own cap |
| Start | every service recreated, sampled each second, 7 min | | | |

A service's need is its peak over the rest, the reference rate and the journeys, so the journeys decide
it: 6 072 MiB resident and 3 220 MiB cold over every service at 250/s, 6 077 and 3 219 at 500/s, the
reference rate. The platform's budget, with the headroom, the margins, the probes and the reserve, is
7 824 MiB of memory; with one authoring session's guarantee, more than the 8 GB node has, its floor
(ADR-0050 dec 6). Every service's resident and cold need, the sessions, the reserve and the probe are
recorded in `deploy/host-memory.toml`, each with its measurement; this page holds how they are measured.

| Part | Counted from cgroup v2 | Must sit in |
|---|---|---|
| Resident | peak of `anon` + `shmem` + `kernel` + `file_mapped` beyond `shmem` over the windows after the reclaim | Memory |
| Cold | peak of resident + `memory.swap.current` over the windows and the start, less the resident peak | Swap |

Without the reclaim, a host with memory to spare leaves untouched pages in place, and they read as
resident: the same run measured 7 641 MiB resident. A start touches every page once and is over in
seconds: its peak counts toward what a service may hold (memory and swap), not toward what it keeps in
memory. Sampled every 10 s, a start's peak is missed: the start is sampled each second.

Load raises the resident need by about 0.7 GB between rest and 500/s, most of it in the two Spark jobs.
The journeys raise it by 1.4 GB more: `ml-service-api` (+528 MiB, training and inference), `timescaledb`
(+195 MiB), `notebook-hub`, `mlflow`, `ops-agent` and `prometheus`; so the need always includes them.
Outside the containers: Docker, containerd and the kernel's memory outside every cgroup, and the backup
as the one-shot job beside the platform.

A healthcheck that runs Python starts an interpreter in the container every interval, too short for the
sampler: it is measured on its own, as `docker exec <container> python -c "import resource, urllib.request;
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)"` (MiB).

### Measure the need

| Step | Command |
|---|---|
| Loose limits (keeps the heaps) | `python3 scripts/host-memory.py`, then in the block set every `*_MEM_LIMIT` to `4096m`, every `*_MEMSWAP_LIMIT` to `8192m` (a memory-and-swap limit equal to the memory limit allows no swap: the cold pages stay in memory and the host thrashes) and `NOTEBOOK_MEMORY_SHARE_MIB` to one that admits the journeys' sessions; `docker compose` with every profile the host runs (`--profile notebooks --profile ops-agent`) `up -d`; check each container's `HostConfig.Memory` and `HostConfig.MemorySwap` |
| Start the sampler (Docker host) | `bash tests/performance/sample_memory.sh /tmp/memory.log &` |
| Reclaim | as root, for every container: `echo 8G > /sys/fs/cgroup/system.slice/docker-<id>.scope/memory.reclaim` (an I/O error means it reclaimed less than asked); the windows start after it |
| Windows | rest 5 min; the reference rate for 5 min ([reproduce](#reproduce)); each profile's [session journey](#a-notebook-session), its sessions stopped, then `scripts/box-e2e.sh`, with ingest beside them (sessions left running take the share the suite's own need) |
| Start | before the reclaim: a second sampler at 1 s (`sample_memory.sh <log> 1`), then `up -d --force-recreate` of every long-running service; 5 min |
| The need's lines | `python3 tests/performance/memory_need.py <logs joined> <start>:<end> … --start <start>:<end> --readings-per-second <rate> --host-mib <MemTotal MiB>` |
| Restore | `python3 scripts/host-memory.py`; the same `up -d` |

### A notebook session

A session's need is measured per profile on the reference journey (ADR-0050 dec 5), with the platform
under loose limits and the hub given a pool that admits it. Its resident part times the headroom is what
the notebook pool guarantees a session of the profile; it has no size, and may grow into the pool.

| Profile | Reference journey | Resident | Cold |
|---|---|---|---|
| Authoring | JupyterLab opened; `clients/python/examples/getting-started.ipynb` over a week of the tenant's readings in a kernel that stays alive; then the features, the fit and a summary of the frame again | 570 MiB | 1 077 MiB |
| Analytics | the reports page opened, and again after the reclaim | 51 MiB | 112 MiB |

Measured 2026-11-26 on the 8 GB node, three runs; the table holds each part's highest. The authoring need
follows the week's rows: 350 MiB resident over 1.6 million rows, 450 in a second run, 570 over
3.1 million. The first load peaks at 1 647 MiB over 3.1 million rows: like a start, it counts toward what
the session may hold, not toward what it keeps in memory.

| Step | Command |
|---|---|
| Sampler at 1 s (Docker host) | `bash tests/performance/sample_memory.sh /tmp/session.log 1 &` |
| Start each profile's session | sign in as an engineer and as an observer, open notebooks |
| Loose limits for the session | `docker update --memory 3000m --memory-swap 6000m <session container>` |
| The journey (authoring) | `docker cp` the notebook and `tests/performance/notebook_session.py` into the container; `docker exec -d <container> python /tmp/notebook_session.py /tmp/getting-started.ipynb`; wait for `notebook-done` |
| Idle, then reclaim | 2 min; as root, `echo 3G > /sys/fs/cgroup/system.slice/docker-<id>.scope/memory.reclaim` for each session (an I/O error means it reclaimed less than asked); note the second |
| Use | `docker exec <container> touch /tmp/use-now`; open the reports page again; 1 min |
| The session's lines | `python3 tests/performance/session_need.py /tmp/session.log --container <container> --profile <profile> --reclaimed <second>` |

## Raising capacity

| Option | Effect |
|---|---|
| More cores on the host | Raises the CPU ceiling; the ingest path and Spark scale with cores |
| More memory | The platform needs 7 824 MiB of memory; what a host has beyond it is the notebook pool ([memory](#memory)), which a session needs 627 MiB of (authoring) or 72 (analytics) to start: a 9 GB host about 1.4 GB, a 12 GB host about 4.4 GB. A session may grow into the whole pool, so a larger host gives the sessions more room and room for more of them. With enough swap, the platform's own need does not grow with the host |
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
| Sensor map | JSON object `name -> sensor UUID` of sensors of an equipment provisioned for the test, in the device's tenant; never a tenant's own sensors, whose charts and data the run's readings would join |
| Start the sampler (Docker host) | `bash tests/performance/sample_host.sh /tmp/sample.log &` |
| Load, one process per 125 readings/s | `python3 tests/performance/loadgen.py --url https://<host>:8443/ingest --cert <crt> --key <key> --ca <edge-ca.crt> --sensor-map <map.json> --equipment-id <uuid> --site-id <site> --rate 125 --duration 120 --run-start <now, ISO with offset> --csv out-K.csv --workers 2 --worker K` (K = 0, 1 for 250 readings/s) |
| Count landed rows | `SELECT count(*) FROM "<tenant-schema>".sensor_measurements WHERE sensor_id IN (<the sensor map's UUIDs>) AND time >= '<run-start>' AND time < '<run-start>'::timestamptz + '<sent × step_us> microseconds'::interval` (`sent` summed over the workers, each sending the same count; `step_us` from the summary) |
| Stop the sampler | `kill %1` in the sampler's shell |
| Summarize host and containers | `python3 tests/performance/analyze.py /tmp/sample.log <start-epoch> <end-epoch>` |

Reading `n` is stamped `--run-start` plus `n` steps of 1 / (rate × workers) seconds: the run's readings
span its duration as a device's would, and the landed count for any prefix of a run is exact. Server-side latency is `http_request_duration_seconds{handler="/ingest"}` in Prometheus.

Symptoms, causes and fixes of a load run are rows of the infrastructure agent's runbook data,
`services/ops_agent/runbook.toml`, following ADR-0009 dec 6.
