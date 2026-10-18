<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Capacity

Measured ingest capacity of the Compose stack on one host, how to reproduce it, and how to raise it.

## Measured on 8 GB RAM, 4 cores

Compose stack with the default [performance settings](../getting-started.md#performance-settings),
one reading per request through the mTLS edge, one tenant, 11 sensors.

| Rate (readings/s) | Duration | Errors | Ingest p95 | Landed | Host CPU | Verdict |
|---|---|---|---|---|---|---|
| 50 | 120 s | 0 | 29 ms | all | 79% | Pass |
| 100 | 120 s | 0 | 28 ms | all | 72% | Pass |
| 250 | 120 s | 0 | 40 ms | all | 82% | Pass |
| 250 | 60 min | 0 | 46 ms | all | 80% | Pass |
| 350 | 120 s | 0 | 140 ms | all | 81% | Pass |
| 500 (offered) | 120 s | 10 (502) | 5.9 s | all accepted | 94% | 435/s achieved |

| Pass criterion | Value |
|---|---|
| Errors | 0 non-2xx |
| Landed | every accepted reading in `sensor_measurements` |
| Ingest p95 | < 500 ms at the device |
| Containers | no restart, no out-of-memory kill |

**Sustained capacity: 250 readings/s for one hour; 350 readings/s passes for 120 s. Ceiling: about
430 readings/s, bound by CPU.** Above the ceiling the edge also runs out of ephemeral ports towards
`ingestion-service` (`connect() … Address not available`, 502), because it opens one upstream
connection per request.

| Rate | Equivalent |
|---|---|
| 250 readings/s | 900 000 per hour, 21.6 million per day |
| 250 sensors | at 1 Hz |
| 2 500 sensors | at 0.1 Hz |

## Where the CPU goes at 350 readings/s

| Container | Cores |
|---|---|
| `ingestion-service` | 0.60 |
| `ingestion-edge` (TLS) | 0.49 |
| `spark-worker` (executors) | 0.48 |
| `alert-service-detector` | 0.45 |
| `kafka` | 0.17 |
| `spark-streaming`, `spark-aggregations`, `timescaledb`, `minio` | 0.11–0.12 each |

Spark runs at a lower CPU weight than the ingest path (`SPARK_CPU_SHARES`): under contention, ingest
keeps its latency and Kafka buffers what Spark falls behind on.

## Raising capacity

| Option | Effect |
|---|---|
| More cores on the host | Raises the CPU ceiling; the ingest path and Spark scale with cores |
| More memory | Removes swap use under sustained load |
| Kubernetes ([Helm chart](../../deploy/helm/industryflow/README.md)) | Ingestion, edge and Spark scale out ([high availability](high-availability.md)) |

## Reproduce

Tools in `tests/performance/`. Run the generator from a separate machine; on the Docker host run only
the sampler. Never poll `docker stats` during a run: on a small host it starves the ingest edge.

| Step | Command |
|---|---|
| Device certificate | `scripts/device-ca.sh issue …` ([device mTLS](device-mtls.md)) |
| Sensor map | JSON object `name -> sensor UUID` of sensors in the device's tenant |
| Start the sampler (Docker host) | `bash tests/performance/sample_host.sh /tmp/sample.log &` |
| Load, one process per 125 readings/s | `python3 tests/performance/loadgen.py --url https://<host>:8443/ingest --cert <crt> --key <key> --sensor-map <map.json> --equipment-id <uuid> --site-id <site> --rate 125 --duration 120 --run-start <iso-time> --csv out-K.csv --workers 2 --worker K` (K = 0, 1 for 250 readings/s) |
| Count landed rows | `SELECT count(*) FROM "<tenant-schema>".sensor_measurements WHERE time >= '<iso-time>'` |
| Summarize host and containers | `python3 tests/performance/analyze.py /tmp/sample.log <start-epoch> <end-epoch>` |

`loadgen.py` needs `httpx`. Every reading carries a unique timestamp from `--run-start`, so the landed
count is exact. A generator process above 125 readings/s lags its own event loop: achieved rate drops
and latency rises while the server's `/ingest` p95 stays low. Compare with the server-side
`http_request_duration_seconds` before blaming the platform.
