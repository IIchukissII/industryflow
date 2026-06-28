# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Upstream-TLS integration test for the SQL proxy backend (ADR-0017).

The proxy connects to TimescaleDB over TLS: it sends a Postgres SSLRequest, upgrades the socket,
and verifies the server certificate against the internal CA before the StartupMessage. There is
no real Postgres here — instead a *fake Postgres-TLS server* (an asyncio server with a throwaway
trustme-issued cert) speaks just enough of the wire protocol to exercise the negotiation,
handshake, trust-anchor check, and StartupMessage-over-TLS end to end:

  client (PostgresBackend.connect_privileged)        fake server
        --- SSLRequest ----------------------->
        <-- 'S' -------------------------------
        ===== TLS handshake (verify CA) =======
        --- StartupMessage (encrypted) ------->   (asserts it arrives over TLS)
        <-- AuthenticationOk, ReadyForQuery ---

It validates the real `loop.start_tls` upgrade and that verify-full rejects an untrusted cert and
a server that declines TLS — without a database, so it runs in the ordinary `unit-tests` CI job.
"""
import asyncio
import os
import ssl
import struct
import sys
import uuid

import pytest
import trustme

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import server  # noqa: E402
import wire  # noqa: E402
from binding import SqlBinding  # noqa: E402


class _DummyClientWriter:
    """Stand-in for the kernel-facing writer; connect_privileged never touches the client side."""

    def is_closing(self):
        return False

    def close(self):
        pass


async def _start_fake_pg(server_ctx, *, decline_tls=False):
    """A fake Postgres that negotiates TLS then completes a trivial (trust-auth) startup.

    Returns (host, port, server, received, handlers) where `received` is a dict the handler fills
    with the StartupMessage params it read *after* the TLS upgrade (proof the startup crossed
    encrypted), and `handlers` is the list of spawned handler tasks for clean teardown.
    """
    received = {}
    handlers = []

    async def handle(reader, writer):
        try:
            sslreq = await reader.readexactly(8)  # SSLRequest: len=8, code
            assert struct.unpack("!II", sslreq) == (8, wire.SSL_REQUEST_CODE)
            if decline_tls:
                writer.write(b"N")
                await writer.drain()
                return
            writer.write(b"S")
            await writer.drain()

            loop = asyncio.get_event_loop()
            transport = writer.transport
            protocol = transport.get_protocol()
            new_t = await loop.start_tls(transport, protocol, server_ctx, server_side=True)
            writer._transport = new_t
            reader._transport = new_t

            header = await reader.readexactly(4)
            (length,) = struct.unpack("!I", header)
            body = await reader.readexactly(length - 4)
            _code, params = wire.parse_startup_packet(body)
            received.update(params)

            writer.write(wire.build_authentication_ok())
            writer.write(wire.build_ready_for_query())
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass

    def spawn(reader, writer):
        handlers.append(asyncio.ensure_future(handle(reader, writer)))

    srv = await asyncio.start_server(spawn, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    return "localhost", port, srv, received, handlers


async def _teardown(srv, handlers):
    """Stop accepting and cancel/drain any in-flight handler tasks — no pending tasks at loop
    close (e.g. a server-side handshake the client aborted in the untrusted-cert case)."""
    srv.close()
    for task in handlers:
        task.cancel()
    await asyncio.gather(*handlers, return_exceptions=True)


def _server_ctx(server_cert):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_cert.configure_cert(ctx)
    return ctx


def _backend(host, port, ssl_context):
    return server.PostgresBackend(
        SqlBinding(user="alice", company_id=str(uuid.uuid4())),
        asyncio.StreamReader(),
        _DummyClientWriter(),
        host=host, port=port, user="notebook_sql_proxy", password="pw", database="industryflow",
        ssl_context=ssl_context, require_tls=True,
    )


@pytest.mark.asyncio
async def test_verify_full_connects_over_tls(tmp_path):
    ca = trustme.CA()
    ca_path = str(tmp_path / "ca.pem")
    ca.cert_pem.write_to_path(ca_path)
    host, port, srv, received, handlers = await _start_fake_pg(_server_ctx(ca.issue_cert("localhost")))

    ctx = server.build_upstream_ssl_context("verify-full", ca_path)
    backend = _backend(host, port, ctx)
    try:
        await backend.connect_privileged()      # SSLRequest → TLS → startup → AuthOk/ReadyForQuery
        await backend.close()
    finally:
        await _teardown(srv, handlers)

    # The StartupMessage was read by the server only after the TLS upgrade succeeded.
    assert received.get("user") == "notebook_sql_proxy"
    assert received.get("database") == "industryflow"


@pytest.mark.asyncio
async def test_verify_full_rejects_untrusted_cert(tmp_path):
    server_ca = trustme.CA()                     # the cert the fake server presents
    other_ca = trustme.CA()                      # a DIFFERENT root the client trusts
    other_path = str(tmp_path / "other-ca.pem")
    other_ca.cert_pem.write_to_path(other_path)
    host, port, srv, _, handlers = await _start_fake_pg(_server_ctx(server_ca.issue_cert("localhost")))

    ctx = server.build_upstream_ssl_context("verify-full", other_path)
    backend = _backend(host, port, ctx)
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            await backend.connect_privileged()
        await backend.close()
    finally:
        await _teardown(srv, handlers)


@pytest.mark.asyncio
async def test_required_tls_refuses_when_server_declines(tmp_path):
    ca = trustme.CA()
    ca_path = str(tmp_path / "ca.pem")
    ca.cert_pem.write_to_path(ca_path)
    host, port, srv, _, handlers = await _start_fake_pg(
        _server_ctx(ca.issue_cert("localhost")), decline_tls=True
    )

    ctx = server.build_upstream_ssl_context("verify-full", ca_path)
    backend = _backend(host, port, ctx)
    try:
        with pytest.raises(ConnectionError):
            await backend.connect_privileged()   # server replied 'N' but verify-full requires TLS
        await backend.close()
    finally:
        await _teardown(srv, handlers)
