<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Dead letters

Inspecting, triaging and replaying readings the stream could not process
([ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md) dec 3). Nothing consumes the dead-letter
topics automatically; an operator handles each letter.

## Topics

| Topic | Written by | Key |
|---|---|---|
| `sensor-data-raw.measurements.dlq` (`KAFKA_TOPIC_MEASUREMENTS_DLQ`) | `spark-streaming` | The reading's tenant |
| `sensor-data-raw.dlq` (`KAFKA_TOPIC_DLQ`) | `alert-service-detector` | None |

A topic is created by its first letter. Every letter is one JSON envelope:

| Field | Content |
|---|---|
| `original` | The message as received, text (hex when not UTF-8) |
| `reason` | Why it was dead-lettered |
| `consumer` | `spark-streaming` or `alert-service-detector` |
| `topic`, `partition`, `offset` | Where the original was read |

A letter is tenant data. It stays in Kafka and reaches no metric, log or dashboard; the alert counts
letters by topic only ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 5).
Letters expire with the topic's retention (broker default).

## Alert

`IndustryFlowDeadLetteredReadings` fires while a letter reached a dead-letter topic in the last 15m,
including a topic's first letter, on Compose and Kubernetes
([`infrastructure/prometheus/rules/platform.yml`](../../infrastructure/prometheus/rules/platform.yml)).
It clears 15m after the last letter. Replaying does not clear it: letters are never removed.

## Commands

`scripts/dead-letters.sh` runs inside the Kafka container with its own tools.

| Task | Compose |
|---|---|
| Letter count per topic | `docker compose exec -T kafka sh -s -- topics < scripts/dead-letters.sh` |
| List letters | `docker compose exec -T kafka sh -s -- show <topic> [-p <partition>] [-o <offset>] [-n <count>] < scripts/dead-letters.sh` |
| Preview a replay | `docker compose exec -T kafka sh -s -- replay <topic> -o <first> -t <last> [-p <partition>] [-r <reason>] < scripts/dead-letters.sh` |
| Replay | the same with `--yes` |

Kubernetes: `kubectl -n <namespace> exec -i <kafka-pod> -- sh -s -- <command> < scripts/dead-letters.sh`.

| Replay behaviour | Detail |
|---|---|
| Target | The topic named in the letter's `topic` field; never a `*.dlq` topic |
| Key | The original reading's `sensor_id`, as ingestion keys it |
| Skipped | Letters whose `original` is not a JSON reading with a `sensor_id`; letters from another source topic than the first in the range |
| Repeating a replay | Safe: the source topic's consumers are idempotent (ADR-0005 dec 6, [ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) dec 3) |
| Raw measurements | Written by `spark-streaming` once the cause is fixed |
| Aggregates | A reading older than the aggregation watermark is not aggregated again |
| Alerts | The detector evaluates the reading again; an alert already raised for it is deduplicated |

Record the replayed range (`<topic> <partition>:<first>-<last>`) in the incident: a replay leaves the
letters in place.

## Triage

| `reason` | Cause | Action |
|---|---|---|
| `missing_or_malformed_field`, `invalid_company_id`, `invalid_sensor_or_equipment_id` | The device sent a reading the contract refuses | Fix the device or gateway; not replayable as sent |
| `future_timestamp` | Device clock more than `MAX_FUTURE_SKEW_SECONDS` (300 s) ahead | Fix the device clock; not replayable as sent |
| `unknown_tenant` | No tenant schema for the reading's tenant | Provision the tenant ([user management](user-management.md#tenants), [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md)), then replay; for a deleted tenant, leave it to expire |
| `suspended_tenant` | The tenant is suspended (ADR-0033 dec 3) | Reactivate the tenant, then replay; for a tenant being deleted, leave it to expire |
| `unknown_sensor`, `unknown_equipment` | The sensor or equipment is not registered in the tenant | Register it, then replay |
| Not JSON, or a missing field (`alert-service-detector`) | Same as the malformed reasons above | Fix the device or gateway |
| `retries exhausted: …` (`alert-service-detector`) | Processing failed `KAFKA_MAX_RETRIES` times on something other than an outage | Fix the cause in the detector's logs (`docker compose logs alert-service-detector`), then replay |

A database or Kafka outage never dead-letters a reading: the batch is retried (ADR-0005 dec 3).

## Related

- [Stream processing](../architecture/stream-processing.md#unwritable-readings): which reading is refused, and where.
- [Alerting](../architecture/alerting.md): the detector's failure handling.
- [Monitoring](monitoring.md#alerts): alert delivery.
