# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The one writer of the capability-use record (ADR-0042 dec 4, 7).

The planes append events to a stream in the capability store; this drains it into
``public.capability_use_events`` through ``record_capability_use()``. A consumer group makes delivery
at-least-once across gateway replicas: an entry is acknowledged only after its batch committed, an
entry a crashed consumer left pending is claimed by another after ``CLAIM_IDLE_MS``, and the stream
entry id makes a redelivery a no-op in the table. An entry the contract does not read is logged,
counted and acknowledged, so one malformed append cannot stall the record behind it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any, Iterable, List, Sequence, Tuple

from industryflow_contracts import capability_audit as audit
from prometheus_client import Counter, Gauge
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

logger = logging.getLogger(__name__)

GROUP = "capability-audit-writer"
BATCH = 500
BLOCK_MS = 5000
CLAIM_IDLE_MS = 60_000
RETRY_SECONDS = 5
# Retention (dec 10): how often the record is expired, and the period it keeps, from the environment;
# the database refuses a period under its floor.
EXPIRE_EVERY_SECONDS = 3600
RETENTION_DAYS = int(os.environ.get("CAPABILITY_AUDIT_RETENTION_DAYS", "365"))

EVENTS = Counter("capability_audit_events_total", "Capability-use stream entries the writer handled",
                 ["result"])
BACKLOG = Gauge("capability_audit_backlog",
                "Capability-use stream entries not yet written to the record (ADR-0042 dec 7)")

RECORD = text(
    "SELECT public.record_capability_use(:stream_id, :kind, :plane, CAST(:at AS timestamptz), "
    "CAST(:company_id AS uuid), :user_id, :audience, CAST(:handle_id AS uuid), :digest, :reason, :detail)"
)

EXPIRE = text("SELECT public.expire_capability_use(:days)")

Entry = Tuple[str, Any]


def _params(stream_id: str, event: dict) -> dict:
    return {"stream_id": stream_id, "kind": event["kind"], "plane": event["plane"], "at": event["at"],
            "company_id": event["company_id"], "user_id": event["user"], "audience": event["audience"],
            "handle_id": event["handle_id"], "digest": event["digest"], "reason": event["reason"],
            "detail": event["detail"]}


async def write_batch(engine, entries: Sequence[Entry]) -> List[str]:
    """Write ``entries``; return every id to acknowledge, the unreadable and the refused included.

    The batch is one transaction. When the database refuses it for what a row holds, each row is
    written alone, and a row the database refuses by itself is counted, logged and acknowledged: one
    value the contract let through must not hold back the record behind it. Any other failure (the
    database unreachable) raises: nothing is acknowledged and the batch is delivered again."""
    rows, ack = [], []
    for stream_id, fields in entries:
        try:
            rows.append(_params(stream_id, audit.decode(fields)))
        except (ValueError, KeyError, TypeError) as exc:
            EVENTS.labels("unreadable").inc()
            logger.warning("capability audit: unreadable stream entry %s skipped: %s", stream_id, exc)
        ack.append(stream_id)
    if not rows:
        return ack
    try:
        async with engine.begin() as conn:
            for row in rows:
                await conn.execute(RECORD, row)
        EVENTS.labels("written").inc(len(rows))
    except (DataError, IntegrityError):
        for row in rows:
            try:
                async with engine.begin() as conn:
                    await conn.execute(RECORD, row)
                EVENTS.labels("written").inc()
            except (DataError, IntegrityError) as exc:
                EVENTS.labels("refused").inc()
                logger.error("capability audit: the record refused stream entry %s: %s",
                             row["stream_id"], exc.orig if hasattr(exc, "orig") else exc)
    return ack


def _entries(reply: Any) -> List[Entry]:
    """Entries from an XREADGROUP reply (``[[stream, [(id, fields)…]]]``) or an XAUTOCLAIM one
    (``[next, [(id, fields)…], deleted]``); a claimed entry already trimmed comes back as None."""
    if not reply:
        return []
    if isinstance(reply, dict):  # RESP3: {stream: [batch]} or {stream: batch}
        items = []
        for batch in reply.values():
            items += batch[0] if batch and isinstance(batch[0], list) else batch
        return [(sid if isinstance(sid, str) else sid.decode(), fields) for sid, fields in items if fields]
    if isinstance(reply, (list, tuple)) and len(reply) >= 2 and isinstance(reply[1], list) \
            and not isinstance(reply[0], (list, tuple)):
        items: Iterable = reply[1]
    else:
        items = [e for _stream, batch in reply for e in batch]
    return [(sid if isinstance(sid, str) else sid.decode(), fields)
            for sid, fields in items if fields]


async def _ensure_group(redis) -> None:
    try:
        await redis.xgroup_create(audit.STREAM_KEY, GROUP, id="0", mkstream=True)
    except Exception as exc:  # noqa: BLE001 - redis raises ResponseError("BUSYGROUP …") when it exists
        if "BUSYGROUP" not in str(exc):
            raise


async def _observe_backlog(redis) -> None:
    # Written entries are deleted, so the stream's length is what is not yet in the record, read or
    # not; unlike the group's lag, it is known after a trim too.
    BACKLOG.set(int(await redis.xlen(audit.STREAM_KEY)))


async def drain_once(redis, engine, consumer: str, *, block_ms: int = BLOCK_MS) -> int:
    """One pass: claim what a dead consumer left pending, then read new entries; return how many were
    acknowledged."""
    claimed = _entries(await redis.xautoclaim(audit.STREAM_KEY, GROUP, consumer, CLAIM_IDLE_MS,
                                              start_id="0-0", count=BATCH))
    fresh = _entries(await redis.xreadgroup(GROUP, consumer, {audit.STREAM_KEY: ">"}, count=BATCH,
                                            block=None if claimed else block_ms))
    entries = claimed + fresh
    if not entries:
        return 0
    ack = await write_batch(engine, entries)
    if ack:
        # Acknowledged, then removed: the stream keeps only the backlog (dec 7), not a copy of the record.
        await redis.xack(audit.STREAM_KEY, GROUP, *ack)
        await redis.xdel(audit.STREAM_KEY, *ack)
    return len(ack)


async def expire(engine, days: int = RETENTION_DAYS) -> int:
    """Remove what the record keeps no longer (dec 10); every replica may run it, and it is idempotent."""
    async with engine.begin() as conn:
        removed = (await conn.execute(EXPIRE, {"days": days})).scalar() or 0
    if removed:
        logger.info("capability audit: %d records older than %d days expired", removed, days)
    return int(removed)


async def run(redis, engine) -> None:
    """The gateway's background writer, one consumer per process."""
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    logger.info("capability audit writer started as %s", consumer)
    next_expiry = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            await _ensure_group(redis)
            while True:
                # Observed before the write, so a writer the database keeps refusing still reports the
                # backlog it is building (dec 7).
                await _observe_backlog(redis)
                await drain_once(redis, engine, consumer)
                if loop.time() >= next_expiry:
                    # Scheduled before the attempt: an expiry the database refuses is retried in an
                    # hour, not on every pass of the writer.
                    next_expiry = loop.time() + EXPIRE_EVERY_SECONDS
                    await expire(engine)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the record waits in the stream; retry after a pause
            EVENTS.labels("retry").inc()
            logger.exception("capability audit writer failed; retrying in %ss", RETRY_SECONDS)
            await asyncio.sleep(RETRY_SECONDS)
