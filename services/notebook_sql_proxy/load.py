# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""How long a session's statements run and its transactions stay idle (ADR-0049 dec 3, 9).

The relay stays byte-transparent: it reads only where each message begins and ends, in both directions,
never what a statement says. A statement is under way from the kernel's first message after the
database last said ReadyForQuery until it says so again; every simple Query and every Sync the kernel
sends is answered by exactly one ReadyForQuery, so a pipelined client's next statement starts its own
time when the one before it is answered (a FunctionCall is answered so too, so it is counted with
them). ReadyForQuery also says whether a transaction is open: one
that stays open with nothing under way is an idle transaction.

A statement past its cap is cancelled the way a client cancels one, by a CancelRequest quoting the
database session's key, and the error the database answers with is rewritten to name the cap. If the
statement is still under way a grace period later (the kernel holds an unfinished extended-protocol
exchange open, which keeps its transaction as an idle one does), the session ends. A transaction idle
past its cap ends the session. Ending one tells the kernel why, with a FATAL error naming the cap, sent
where a message ends so the kernel's driver can read it.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from industryflow_contracts import quotas

import wire

# How long a cancelled statement may keep running before its session ends.
CANCEL_GRACE_SECONDS = 10.0

# The SQLSTATEs: a cancelled statement (query_canceled), a transaction idle too long (the code Postgres
# gives its own idle-in-transaction timeout).
QUERY_CANCELED = "57014"
IDLE_IN_TRANSACTION_TIMEOUT = "25P03"

# A message held back to be read whole is at most this long; a longer one passes as it comes.
HOLD_MAX = 1 << 16

# ReadyForQuery's transaction status: in a transaction, or in a failed one. Idle is wire.TXN_IDLE.
_IN_TRANSACTION = (b"T", b"E")
# What the kernel sends that the database answers with one ReadyForQuery each: a simple Query, a Sync,
# a FunctionCall. One left out would let its ReadyForQuery stop the clock of the statement after it.
_ANSWERED = (wire.MSG_QUERY, b"S", b"F")


class Frames:
    """Where each message of one direction begins and ends, across reads that split them anywhere.

    ``feed`` returns the bytes to pass on. A message whose type is in ``hold`` is held until whole and
    handed to ``on_message(type, message)``, whose return is passed on in its place; every other message
    is passed on as it arrives and only reported, by type, when it begins (``on_begin``).
    """

    def __init__(self, *, hold=(), on_message=None, on_begin=None):
        self._hold = tuple(hold)
        self._on_message = on_message
        self._on_begin = on_begin
        self._head = bytearray()
        self._type: Optional[bytes] = None
        self._left = 0
        self._held: Optional[bytearray] = None

    @property
    def at_boundary(self) -> bool:
        """Whether what has been passed on ends where a message ends."""
        return self._type is None and not self._head and self._held is None

    def feed(self, chunk: bytes) -> bytes:
        out = bytearray()
        i, n = 0, len(chunk)
        while i < n:
            if self._type is None:
                take = min(5 - len(self._head), n - i)
                self._head += chunk[i:i + take]
                i += take
                if len(self._head) < 5:
                    break
                type_byte = bytes(self._head[:1])
                length = int.from_bytes(self._head[1:5], "big")
                if length < 4:
                    raise wire.ProtocolError(f"implausible message length: {length}")
                self._type, self._left = type_byte, length - 4
                if self._on_begin is not None:
                    self._on_begin(type_byte)
                if type_byte in self._hold and length + 1 <= HOLD_MAX:
                    self._held = bytearray(self._head)
                else:
                    out += self._head
                self._head.clear()
            take = min(self._left, n - i)
            if self._held is not None:
                self._held += chunk[i:i + take]
            else:
                out += chunk[i:i + take]
            i += take
            self._left -= take
            if self._left == 0:
                if self._held is not None:
                    message = bytes(self._held)
                    self._held = None
                    out += self._on_message(self._type, message) if self._on_message else message
                self._type = None
        return bytes(out)


class SessionLoad:
    """The statement and idle-transaction clocks of one session, against its tenant's caps.

    ``client_message`` and ``upstream_message`` are fed by the relay; ``tick`` is called every second and
    returns the cap that ends the session, if one does. ``cancel`` cancels the statement under way;
    ``refused(cap)`` counts a cancellation or an ending against the tenant (dec 5).
    """

    def __init__(self, statement_seconds: Optional[int], idle_transaction_seconds: Optional[int], *,
                 cancel: Callable[[], Awaitable[None]], refused: Callable[[str], Awaitable[None]],
                 clock: Callable[[], float] = time.monotonic):
        self._statement = statement_seconds
        self._idle = idle_transaction_seconds
        self._cancel = cancel
        self._refused = refused
        self._clock = clock
        self._outstanding = 0
        self._busy_since: Optional[float] = None
        self._status = wire.TXN_IDLE
        self._idle_since = clock()
        self._cancelled_at: Optional[float] = None
        self._rewrite = False

    def client_message(self, type_byte: bytes) -> None:
        """The kernel began a message: something is under way until the database is ready again."""
        if type_byte in _ANSWERED:
            self._outstanding += 1
        if self._busy_since is None:
            self._busy_since = self._clock()

    def upstream_message(self, type_byte: bytes, message: bytes) -> bytes:
        """A held message from the database, whole: ReadyForQuery ends what was under way; the error a
        cancellation of ours drew is rewritten to name the cap."""
        if type_byte == wire.MSG_READY_FOR_QUERY:
            self._status = message[5:6]
            self._outstanding = max(0, self._outstanding - 1)
            self._cancelled_at = None
            self._rewrite = False
            now = self._clock()
            # A pipelined client's next statement was waiting: its time starts now.
            self._busy_since = now if self._outstanding else None
            self._idle_since = now
            return message
        if type_byte == wire.MSG_ERROR_RESPONSE and self._rewrite:
            fields = wire.parse_error_response(message[5:])
            if fields.get("C") == QUERY_CANCELED:
                self._rewrite = False
                return wire.build_error_response(
                    f"statement cancelled: it ran past {self._statement} s, your organisation's limit "
                    f"({quotas.SQL_STATEMENT_SECONDS})", code=QUERY_CANCELED, severity="ERROR")
        return message

    async def tick(self) -> Optional[str]:
        now = self._clock()
        if self._busy_since is not None:
            if self._statement is None:
                return None
            if self._cancelled_at is None:
                if now - self._busy_since > self._statement:
                    self._cancelled_at = now
                    self._rewrite = True
                    await self._refused(quotas.SQL_STATEMENT_SECONDS)
                    await self._cancel()
            elif now - self._cancelled_at > CANCEL_GRACE_SECONDS:
                return quotas.SQL_STATEMENT_SECONDS
            return None
        if self._idle is not None and self._status in _IN_TRANSACTION and now - self._idle_since > self._idle:
            await self._refused(quotas.SQL_IDLE_TRANSACTION_SECONDS)
            return quotas.SQL_IDLE_TRANSACTION_SECONDS
        return None

    def ending(self, cap: str) -> bytes:
        """The FATAL error that tells the kernel which cap ended its session."""
        if cap == quotas.SQL_IDLE_TRANSACTION_SECONDS:
            return wire.build_error_response(
                f"session ended: a transaction stayed idle past {self._idle} s, your organisation's limit "
                f"({cap})", code=IDLE_IN_TRANSACTION_TIMEOUT)
        return wire.build_error_response(
            f"session ended: a statement kept running past {self._statement} s after it was cancelled at "
            f"your organisation's limit ({cap})", code=QUERY_CANCELED)
