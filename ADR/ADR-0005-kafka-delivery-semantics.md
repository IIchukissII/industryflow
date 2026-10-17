<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0005 — Kafka delivery semantics: at-least-once with idempotent consumers

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-17
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md)
- **Related:** [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (idempotent stream
  sinks), [ADR-0029](ADR-0029-spark-major-upgrade-checkpoint-cutover.md) (aggregation start offset)

## Context

The platform exists to alert on readings, so a reading lost without trace is its worst failure; one
processed twice is harmless if its effect is idempotent. Stages with different delivery semantics give
no end-to-end guarantee, and exactly-once adds coordination to every stage.

## Decision

1. **The Kafka pipeline provides at-least-once delivery.** Every consumer is bound by it.
2. **Consumers commit offsets manually, after the work is durably done.**
3. **A processing failure never advances the offset.** An invalid message is dead-lettered at once,
   with its reason, to a topic owned by one consumer, and an unknown failure after bounded retries; an
   infrastructure failure is retried with capped backoff and never dead-lettered.
4. **A consumer that is the only path by which a reading is kept starts a new group at the earliest
   offset.** A consumer of derived output may start at the tail, as the aggregation stream does
   (ADR-0029).
5. **Producers acknowledge fully and retry.**
6. **Each consumer makes its effect idempotent.** An exact redelivery derives the same alert identity
   and is a no-op; another alert of the same scope within the cooldown of event time is suppressed,
   checked against stored alerts and serialised per scope in the database.

## Alternatives

| Alternative | Why rejected |
|---|---|
| At-most-once (auto-commit, swallowed errors, start at latest) | Silently loses readings and their alerts |
| Exactly-once via Kafka transactions | Coordination cost to remove duplicates idempotent sinks already neutralise |
| Auto-commit with per-interval processing | The timer is independent of per-message success |
| Per-consumer delivery policy | Leaves no end-to-end guarantee |

## Consequences

**Positive**
- Every reading is processed or visibly dead-lettered; a dependency outage delays work instead.
- A crash resumes from the last processed message.

**Negative**
- Every consumer must be idempotent, and manual commits cost throughput.
- Dead-letter topics need monitoring and a handling process.
- The aggregation stream loses the windows open at a reset (ADR-0029).

## Deferred

| Open question | Settled by |
|---|---|
| Exactly-once for a path | A future sink that cannot be made idempotent |
| Partition counts and consumer parallelism | Measured throughput |

## References

- `services/alert_service/worker/kafka_consumer.py`, `services/alert_service/config.py`: commit,
  retry, dead-letter and cooldown (decisions 2, 3, 6).
