<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: MIT
-->

# ADR-0005: Kafka delivery semantics — at-least-once with idempotent consumers

- **ID:** ADR-0005
- **Status:** Proposed
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing — pending)
- **Companions:** ADR-0006 (Spark windowing & idempotent writes — pending)

## Context and problem

Sensor data flows ingestion → Kafka → consumers (the alert-detection worker and the Spark streaming/aggregation jobs). The contract for what happens to a message on failure — is it guaranteed to be processed, or can it be lost, or processed twice? — has never been decided, and the consumers disagree with each other and with what an alerting platform needs.

The alert worker's Kafka consumer is configured **at-most-once**: `enable_auto_commit=True` commits offsets on a timer independent of whether processing succeeded, per-message exceptions are caught and swallowed while the offset still advances, and `auto_offset_reset='latest'` discards any backlog on first start (`services/alert_service/worker/kafka_consumer.py:36,82-83`). The result is silent loss: a crash between auto-commit and processing, or any handler exception, drops the reading — and with it the alert it would have raised. For a system whose stated purpose is to detect anomalies and alert on them, silently losing the input is the most damaging failure mode it can have, and it is the current default.

The producer side is healthier — the ingestion producer already uses `acks=all` — but this is incidental, not a recorded contract, and nothing guarantees the consumers honour it. Meanwhile the Spark jobs make their own independent choices about offsets and error handling (see ADR-0006). There is no single statement of the delivery guarantee the pipeline provides, so each stage picks one and they do not compose.

This ADR fixes the delivery semantics for the whole Kafka pipeline. It is a companion to ADR-0006: the guarantee chosen here (at-least-once) is only safe because the sinks are made idempotent there.

## Decision drivers

- **Sensor readings and alerts must not be silently dropped.** The platform's value is detecting and alerting; losing input without a trace defeats it. A lost message must at worst become a retry or a dead-letter, never a silent gap.
- **Duplicates are tolerable; loss is not.** Re-processing a reading twice is acceptable if the sinks are idempotent; failing to process it at all is not. This asymmetry points directly at at-least-once.
- **The guarantee must be uniform across consumers.** A pipeline whose stages choose different semantics has no end-to-end guarantee. One contract, honoured by every consumer.
- **Offsets must track processing, not wall-clock.** An offset may only advance once the work it represents is durably done; committing on a timer divorced from processing is the root of the silent-loss bug.
- **Exactly-once is more machinery than this pipeline needs.** Kafka transactions/EOS add coordination cost and complexity that idempotent sinks make unnecessary here.

## Decision

1. **The Kafka pipeline provides at-least-once delivery.** Every message produced to a sensor/alert topic is guaranteed to be processed at least once; it may occasionally be processed more than once, and consumers/sinks must tolerate that (idempotency is decided for the Spark sinks in ADR-0006 and applies to alert writes here).

2. **Consumers commit offsets manually, after successful processing.** `enable_auto_commit` is disabled; a consumer commits a message's (or batch's) offset only after the work for it is durably complete. Timer-based auto-commit is removed (closes the silent-loss root cause).

3. **A processing failure must not advance the offset.** Exceptions in a handler are not swallowed past the commit. A failed message is retried and, if it remains unprocessable, routed to a dead-letter destination for inspection — never dropped while its offset moves forward.

4. **New consumer groups over durable data start from the earliest offset.** Consumers responsible for not losing data use `auto_offset_reset=earliest` so a first start or a group reset does not skip the existing backlog. `latest` is reserved for genuinely ephemeral, loss-tolerant consumers, of which the alert worker is not one.

5. **Producers acknowledge fully and retry.** Producers to the pipeline use `acks=all` with retries enabled, so a message is not considered produced until it is durably replicated. The ingestion producer's existing `acks=all` is hereby the recorded contract, not an incidental setting.

6. **Idempotency is the consumer's responsibility, given at-least-once.** Because redelivery is expected, each consumer must make its effect idempotent: Spark sinks via ADR-0006, and alert writes via a dedup/cooldown key so a redelivered reading does not raise a duplicate alert.

## Alternatives considered

**A. At-most-once (status quo).** *Rejected:* it silently loses readings and the alerts they would raise — the worst failure mode for an alerting platform, and the live behaviour of the alert worker today.

**B. Exactly-once via Kafka transactions / EOS.** *Rejected:* it adds transactional-producer and read-process-write coordination overhead to every stage to eliminate duplicates that idempotent sinks already neutralize. The cost is not justified when at-least-once + idempotency reaches the same observable result more simply.

**C. Keep auto-commit but process synchronously before each commit interval.** *Rejected:* it still races — the commit timer is independent of per-message success, so a crash or a handler error between processing and the next commit (or an exception inside the interval) still drops or mis-commits messages. Tying the offset to processing requires manual commits, not a tighter timer.

**D. At-least-once but leave each consumer to choose its own offset/error policy.** *Rejected:* that is the current non-composition. A pipeline guarantee that some stages opt out of is not a guarantee; decision 1 binds every consumer.

## Consequences

### Positive

- No silent message loss: every reading is processed or visibly dead-lettered, and the alert it implies is not quietly missed.
- The pipeline has one stated, uniform delivery contract that the stages compose around, rather than three incompatible local choices.
- Offsets reflect real processing progress, so consumer lag and replay behave predictably and a crash resumes from the last *processed* message, not the last *timed* commit.

### Negative

- Duplicates are now expected, so every consumer must be idempotent; a consumer that is not idempotent will double-count or double-alert. This obligation is real and is carried by ADR-0006 and the alert-dedup requirement.
- Manual commits and `acks=all` cost some throughput versus fire-and-forget auto-commit — an intentional trade of speed for not losing data.
- A dead-letter destination and a retry policy must be operated and monitored; messages can accumulate there and need a handling process.

## Deferred decisions

- **Dead-letter mechanism.** Whether failed messages go to a dedicated DLQ topic, a table, or another sink, and the retry/backoff policy before a message is dead-lettered, are implementation concerns owned by the consumer configuration, not fixed here.
- **Alert deduplication key and cooldown window.** The exact key and suppression window that make alert writes idempotent (decision 6) are an alert-service design detail.
- **Exactly-once, if it ever becomes necessary.** If a future sink cannot be made idempotent, revisiting EOS for that path is left open.
- **Topic/partition and consumer-group sizing.** Throughput tuning (partition counts, consumer parallelism) is a configuration concern, not a semantics decision.

## References

- IndustryFlow review (2026-06-26), alert-worker at-most-once consumer (silent alert loss) — internal report.
- ADR-0006 — Spark windowing & idempotent writes; provides the idempotent sinks that make at-least-once safe.
- ADR-0002 — ingestion authentication; the ingestion producer is the entry point whose `acks=all` contract is recorded here.
