# IndustryFlow Throughput Scalability Test Plan

**Version:** 1.0.0
**Architecture:** Schema-per-tenant (v5.0)
**Date:** November 2025

---

## Objective
Demonstrate horizontal scalability by testing system throughput with incremental resource increases.

## Test Matrix

### Phase 1: Kafka Partitions Scaling (Fixed Load: 500 msg/sec)
| Test ID | Kafka Partitions | Ingestion Workers | Duration | Expected Throughput |
|---------|-----------------|-------------------|----------|-------------------|
| K1      | 1               | 1                 | 5 min    | Baseline          |
| K2      | 3               | 1                 | 5 min    | ~3x improvement   |
| K3      | 6               | 1                 | 5 min    | ~6x improvement   |
| K4      | 12              | 1                 | 5 min    | ~12x improvement  |

### Phase 2: Ingestion Workers Scaling (Fixed Load: 500 msg/sec, 12 partitions)
| Test ID | Kafka Partitions | Ingestion Workers | Duration | Expected Throughput |
|---------|--------------|-------------------|----------|-------------------|
| W1      | 3            | 1                 | 5 min    | Baseline (K4)     |
| W2      | 3            | 2                 | 5 min    | ~2x improvement   |
| W3      | 3            | 4                 | 5 min    | ~4x improvement   |
| W4      | 3            | 8                 | 5 min    | ~8x improvement   |

### Phase 3: Saturation Tests (Push to limit)
| Test ID | Kafka Partitions | Ingestion Workers | Target Load | Duration |
|---------|-----------------|-------------------|-------------|----------|
| S1      | 1               | 1                 | Max         | 5 min    |
| S2      | 12              | 1                 | Max         | 5 min    |
| S3      | 12              | 8                 | Max         | 5 min    |

## Metrics Collected
- Throughput (msg/sec)
- Latency (avg, median, p95, max in ms)
- CPU usage (%)
- Memory usage (%)
- Error count
- Consumer lag (from Kafka)

## Current Configuration (Baseline)
- Kafka: 1 partition
- Ingestion Service: 1 worker
- Spark: local[*], batchsize=5000, numPartitions=12
- TimescaleDB: max_connections=400

## Test Execution Order
1. Run K1 (baseline) ✓
2. Increase Kafka partitions, run K2-K4
3. Add Ingestion workers, run W2-W4
4. Run saturation tests S1-S3

Results: `performance_results_<test_id>.csv`
