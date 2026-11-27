# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A session's statements and idle transactions are held to its tenant's caps from outside the session
(ADR-0049 dec 3, 9): message boundaries read on the wire wherever reads split them, a statement past its
cap cancelled with an error naming the cap, a session whose transaction idles past its cap ended with a
FATAL naming it. The last tests drive ``PostgresBackend`` over real sockets against a fake Postgres
whose statements run until cancelled; the live proof is test_sql_proxy_integration.py."""
import asyncio
import os
import socket
import struct
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import binding as b  # noqa: E402
import load  # noqa: E402
import server  # noqa: E402
import wire  # noqa: E402
from industryflow_contracts import quotas  # noqa: E402
from quota import Limits  # noqa: E402

KEY = (4242, 0x0BADBEEF)


def _stream():
    """A ReadyForQuery-ended exchange with a large row between, as a database sends it."""
    return (wire.build_message(b"T", b"\x00\x01col\x00" + b"\x00" * 18)
            + wire.build_message(b"D", b"\x00\x01" + struct.pack("!I", 70000) + b"x" * 70000)
            + wire.build_message(b"C", b"SELECT 1\x00")
            + wire.build_ready_for_query(b"T"))


@pytest.mark.parametrize("step", [1, 2, 3, 5, 7, 4096, 65536, 1 << 20])
def test_every_split_of_the_stream_passes_the_same_bytes_and_reads_the_same_messages(step):
    data = _stream()
    seen = []
    frames = load.Frames(hold=(b"Z",), on_message=lambda t, m: seen.append((t, m)) or m,
                         on_begin=lambda t: seen.append(t))
    out = b"".join(frames.feed(data[i:i + step]) for i in range(0, len(data), step))
    assert out == data
    assert seen == [b"T", b"D", b"C", b"Z", (b"Z", wire.build_ready_for_query(b"T"))]
    assert frames.at_boundary


def test_a_boundary_is_not_claimed_inside_a_message():
    frames = load.Frames()
    message = wire.build_message(b"D", b"y" * 100)
    frames.feed(message[:3])
    assert not frames.at_boundary
    frames.feed(message[3:50])
    assert not frames.at_boundary
    frames.feed(message[50:])
    assert frames.at_boundary


def test_an_implausible_length_is_a_protocol_error():
    with pytest.raises(wire.ProtocolError):
        load.Frames().feed(b"D\x00\x00\x00\x02")


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _load(statement=10, idle=5):
    clock, cancels, refused = _Clock(), [], []

    async def cancel():
        cancels.append(clock.now)

    async def count(cap):
        refused.append(cap)

    return load.SessionLoad(statement, idle, cancel=cancel, refused=count, clock=clock), clock, cancels, refused


def _ready(status=b"I"):
    return wire.build_ready_for_query(status)


@pytest.mark.asyncio
async def test_a_statement_past_its_cap_is_cancelled_once_and_its_error_names_the_cap():
    session, clock, cancels, refused = _load(statement=10)
    session.client_message(wire.MSG_QUERY)
    clock.now = 10
    assert await session.tick() is None and cancels == []
    clock.now = 10.5
    assert await session.tick() is None
    clock.now = 11
    assert await session.tick() is None
    assert cancels == [10.5] and refused == [quotas.SQL_STATEMENT_SECONDS]
    error = wire.build_error_response("canceling statement due to user request", code="57014", severity="ERROR")
    rewritten = session.upstream_message(b"E", error)
    fields = wire.parse_error_response(rewritten[5:])
    assert fields["C"] == "57014" and fields["S"] == "ERROR"
    assert "10 s" in fields["M"] and quotas.SQL_STATEMENT_SECONDS in fields["M"]
    # Any other error passes as the database wrote it.
    other = wire.build_error_response("division by zero", code="22012", severity="ERROR")
    assert session.upstream_message(b"E", other) == other
    assert session.upstream_message(b"Z", _ready()) == _ready()
    clock.now = 100
    assert await session.tick() is None and cancels == [10.5]


@pytest.mark.asyncio
async def test_a_statement_that_outlives_its_cancel_ends_the_session():
    session, clock, cancels, _ = _load(statement=10)
    session.client_message(b"P")          # an extended exchange, never synced
    clock.now = 11
    await session.tick()
    clock.now = 11 + load.CANCEL_GRACE_SECONDS + 0.1
    assert await session.tick() == quotas.SQL_STATEMENT_SECONDS
    assert wire.parse_error_response(session.ending(quotas.SQL_STATEMENT_SECONDS)[5:])["S"] == "FATAL"


@pytest.mark.asyncio
async def test_a_pipelined_statement_starts_its_time_when_the_one_before_is_answered():
    session, clock, cancels, _ = _load(statement=10)
    session.client_message(wire.MSG_QUERY)
    session.client_message(wire.MSG_QUERY)
    clock.now = 8
    session.upstream_message(b"Z", _ready())     # the first answered; the second now runs
    clock.now = 17
    assert await session.tick() is None and cancels == []
    clock.now = 18.5
    await session.tick()
    assert cancels == [18.5]


@pytest.mark.asyncio
async def test_a_transaction_idle_past_its_cap_ends_the_session_and_an_idle_session_does_not():
    session, clock, _, refused = _load(idle=5)
    session.client_message(wire.MSG_QUERY)
    session.upstream_message(b"Z", _ready(b"I"))
    clock.now = 1000
    assert await session.tick() is None      # no transaction open: idle as long as it likes
    session.client_message(wire.MSG_QUERY)
    session.upstream_message(b"Z", _ready(b"T"))
    clock.now = 1005
    assert await session.tick() is None
    clock.now = 1005.5
    assert await session.tick() == quotas.SQL_IDLE_TRANSACTION_SECONDS
    assert refused == [quotas.SQL_IDLE_TRANSACTION_SECONDS]
    fields = wire.parse_error_response(session.ending(quotas.SQL_IDLE_TRANSACTION_SECONDS)[5:])
    assert fields["C"] == "25P03" and quotas.SQL_IDLE_TRANSACTION_SECONDS in fields["M"]


@pytest.mark.asyncio
async def test_off_caps_never_cancel_or_end():
    session, clock, cancels, _ = _load(statement=None, idle=None)
    session.client_message(wire.MSG_QUERY)
    clock.now = 1e6
    assert await session.tick() is None and cancels == []
    session.upstream_message(b"Z", _ready(b"T"))
    clock.now = 2e6
    assert await session.tick() is None


# --- the backend, over real sockets ----------------------------------------------------------------

class SlowPostgres:
    """A Postgres whose ``SELECT pg_sleep`` runs until cancelled (then answers 57014), whose ``BEGIN``
    opens a transaction, and whose other statements answer at once. ``ignore_cancel`` keeps a sleep
    running through a cancel."""

    def __init__(self, ignore_cancel=False):
        self.ignore_cancel = ignore_cancel
        self.cancels = []
        self._cancelled = asyncio.Event()
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self):
        self._server.close()

    async def _handle(self, reader, writer):
        status = b"I"
        try:
            (length,) = struct.unpack("!I", await reader.readexactly(4))
            body = await reader.readexactly(length - 4)
            code, _params = wire.parse_startup_packet(body)
            if code == wire.CANCEL_REQUEST_CODE:
                self.cancels.append(wire.parse_cancel_request(body))
                if not self.ignore_cancel:
                    self._cancelled.set()
                writer.close()
                return
            writer.write(wire.build_authentication_ok() + wire.build_backend_key_data(*KEY)
                         + wire.build_ready_for_query())
            await writer.drain()
            while True:
                type_byte = await reader.readexactly(1)
                (length,) = struct.unpack("!I", await reader.readexactly(4))
                payload = await reader.readexactly(length - 4)
                if type_byte == wire.MSG_TERMINATE:
                    break
                sql = payload[:-1].decode()
                if sql.startswith("SELECT pg_sleep"):
                    await self._cancelled.wait()
                    self._cancelled.clear()
                    writer.write(wire.build_error_response("canceling statement due to user request",
                                                           code="57014", severity="ERROR"))
                    status = b"E" if status != b"I" else b"I"
                elif sql == "BEGIN":
                    status = b"T"
                    writer.write(wire.build_message(b"C", b"BEGIN\x00"))
                else:
                    writer.write(wire.build_message(b"C", b"SET\x00"))
                writer.write(wire.build_ready_for_query(status))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


async def _session(pg, limits, refused):
    port = await pg.start()
    cfg = server.UpstreamConfig(host="127.0.0.1", port=port, database="industryflow",
                                enrol_user="notebook_sql_proxy", enrol_password="x", tenant_key=b"k" * 32,
                                ssl_context=None, require_tls=False)
    proxy_sock, kernel_sock = socket.socketpair()
    p_reader, p_writer = await asyncio.open_connection(sock=proxy_sock)
    k_reader, k_writer = await asyncio.open_connection(sock=kernel_sock)
    backend = server.PostgresBackend(b.SqlBinding(user="alice", company_id=str(uuid.uuid4())),
                                     p_reader, p_writer, cfg)
    await backend.connect()

    async def count(cap):
        refused.append(cap)

    relay = asyncio.create_task(backend.relay(None, limits=limits, refused=count))
    while (await _message(k_reader))[0] != b"Z":
        pass
    return backend, relay, k_reader, k_writer


async def _message(reader):
    t = await reader.readexactly(1)
    (length,) = struct.unpack("!I", await reader.readexactly(4))
    return t, await reader.readexactly(length - 4)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(server, "LOAD_TICK_SECONDS", 0.05)
    monkeypatch.setattr(load, "CANCEL_GRACE_SECONDS", 0.3)


@pytest.mark.asyncio
async def test_the_backend_cancels_a_statement_at_its_cap_and_the_kernel_reads_why(fast):
    pg, refused = SlowPostgres(), []
    backend, relay, k_reader, k_writer = await _session(pg, Limits(5, 1, 60), refused)
    try:
        k_writer.write(wire.build_query("SELECT pg_sleep(600)"))
        await k_writer.drain()
        t, payload = await asyncio.wait_for(_message(k_reader), 5)
        assert t == b"E"
        fields = wire.parse_error_response(payload)
        assert fields["C"] == "57014" and quotas.SQL_STATEMENT_SECONDS in fields["M"]
        assert (await _message(k_reader))[0] == b"Z"
        assert pg.cancels == [KEY]               # the database session's own key, from the proxy
        assert refused == [quotas.SQL_STATEMENT_SECONDS]
        # The session goes on.
        k_writer.write(wire.build_query("SELECT 1"))
        await k_writer.drain()
        assert (await _message(k_reader))[0] == b"C"
        assert backend.ended_by is None
    finally:
        k_writer.close()
        await asyncio.wait_for(relay, 5)
        await pg.stop()


@pytest.mark.asyncio
async def test_the_backend_ends_a_session_whose_statement_outlives_its_cancel(fast):
    pg, refused = SlowPostgres(ignore_cancel=True), []
    backend, relay, k_reader, k_writer = await _session(pg, Limits(5, 1, 60), refused)
    try:
        k_writer.write(wire.build_query("SELECT pg_sleep(600)"))
        await k_writer.drain()
        t, payload = await asyncio.wait_for(_message(k_reader), 5)
        assert t == b"E" and wire.parse_error_response(payload)["S"] == "FATAL"
        await asyncio.wait_for(relay, 5)
        assert backend.ended_by == quotas.SQL_STATEMENT_SECONDS
    finally:
        k_writer.close()
        await pg.stop()


@pytest.mark.asyncio
async def test_the_backend_ends_a_session_whose_transaction_idles_past_its_cap(fast):
    pg, refused = SlowPostgres(), []
    backend, relay, k_reader, k_writer = await _session(pg, Limits(5, 300, 1), refused)
    try:
        k_writer.write(wire.build_query("BEGIN"))
        await k_writer.drain()
        assert (await _message(k_reader))[0] == b"C"
        assert (await _message(k_reader)) == (b"Z", b"T")
        t, payload = await asyncio.wait_for(_message(k_reader), 5)
        fields = wire.parse_error_response(payload)
        assert t == b"E" and fields["S"] == "FATAL" and fields["C"] == "25P03"
        await asyncio.wait_for(relay, 5)
        assert backend.ended_by == quotas.SQL_IDLE_TRANSACTION_SECONDS
        assert refused == [quotas.SQL_IDLE_TRANSACTION_SECONDS]
    finally:
        k_writer.close()
        await pg.stop()


@pytest.mark.asyncio
async def test_a_pipelined_function_call_does_not_stop_the_clock_of_the_statement_after_it():
    """A FunctionCall is answered by its own ReadyForQuery; uncounted, that answer would read as the end
    of the long statement pipelined behind it, which would then run past its cap."""
    session, clock, cancels, _ = _load(statement=10)
    session.client_message(b"F")
    session.client_message(wire.MSG_QUERY)
    clock.now = 1
    session.upstream_message(b"Z", _ready())     # the FunctionCall answered; the Query still runs
    clock.now = 12
    await session.tick()
    assert cancels == [12]
