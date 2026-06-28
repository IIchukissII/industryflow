# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the frontend handshake (ADR-0015). The kernel↔proxy protocol is driven over in-memory
asyncio pipes, so the handshake — declining SSL, reading the StartupMessage, requesting a
password, extracting the capability handle — is validated without a database. The upstream
``PostgresBackend`` (live relay) is the cluster-bound part and is exercised by the issue #19
integration test, not here.
"""
import asyncio
import json
import os
import ssl
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import binding as b  # noqa: E402
import server  # noqa: E402
import wire  # noqa: E402


def test_ssl_context_disable_is_none():
    assert server.build_upstream_ssl_context("disable", None) is None


def test_ssl_context_require_encrypts_without_verifying():
    ctx = server.build_upstream_ssl_context("require", None)
    assert ctx.verify_mode == ssl.CERT_NONE
    assert ctx.check_hostname is False


def test_ssl_context_verify_full_checks_cert_and_hostname():
    ctx = server.build_upstream_ssl_context("verify-full", None)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_ssl_context_verify_ca_checks_cert_not_hostname():
    ctx = server.build_upstream_ssl_context("verify-ca", None)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is False


async def _pipe_pair():
    """A connected (reader, writer) pair backed by an in-memory transport."""
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport = _MemoryTransport()
    writer = asyncio.StreamWriter(transport, protocol, reader, asyncio.get_event_loop())
    return reader, writer, transport


class _MemoryTransport(asyncio.Transport):
    """Captures everything written, so a test can assert on the proxy's replies."""

    def __init__(self):
        super().__init__()
        self.buffer = bytearray()
        self._closing = False

    def write(self, data):
        self.buffer.extend(data)

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True


def _read_one(buf: bytes):
    return wire.split_message(bytes(buf))


@pytest.mark.asyncio
async def test_authenticate_client_extracts_handle():
    reader, writer, transport = await _pipe_pair()
    # Kernel sends StartupMessage, then (after our auth request) the PasswordMessage = handle.
    reader.feed_data(wire.build_startup_message({"user": "kernel", "database": "industryflow"}))
    reader.feed_data(wire.build_password_message("cap-abc123"))
    reader.feed_eof()

    params, handle = await server.authenticate_client(reader, writer)

    assert handle == "cap-abc123"
    assert params["user"] == "kernel"
    # The proxy asked for a cleartext password.
    type_byte, payload, _ = _read_one(transport.buffer)
    assert type_byte == wire.MSG_AUTHENTICATION
    assert wire.parse_authentication(payload)[0] == wire.AUTH_CLEARTEXT_PASSWORD


@pytest.mark.asyncio
async def test_authenticate_client_declines_ssl_then_proceeds():
    reader, writer, transport = await _pipe_pair()
    import struct

    reader.feed_data(struct.pack("!I", 8) + struct.pack("!I", wire.SSL_REQUEST_CODE))
    reader.feed_data(wire.build_startup_message({"user": "kernel"}))
    reader.feed_data(wire.build_password_message("cap-xyz"))
    reader.feed_eof()

    _params, handle = await server.authenticate_client(reader, writer)

    assert handle == "cap-xyz"
    # First byte the proxy wrote is the SSL decline 'N'.
    assert transport.buffer[0:1] == b"N"


@pytest.mark.asyncio
async def test_handle_client_rejects_invalid_handle_without_backend():
    reader, writer, transport = await _pipe_pair()
    reader.feed_data(wire.build_startup_message({"user": "kernel"}))
    reader.feed_data(wire.build_password_message("not-minted"))
    reader.feed_eof()

    async def store_get(_key):
        return None  # nothing minted → deny

    opened = []

    def backend_factory(binding, r, w):
        opened.append(binding)
        return object()

    await server.handle_client(reader, writer, store_get, backend_factory)

    assert opened == []  # the privileged backend is never opened for a refused handle
    # The kernel received a FATAL ErrorResponse. Skip the AuthenticationCleartextPassword we sent.
    _t, _p, rest = _read_one(transport.buffer)
    type_byte, payload, _ = wire.split_message(rest)
    assert type_byte == wire.MSG_ERROR_RESPONSE
    assert wire.parse_error_response(payload)["C"] == "28000"


@pytest.mark.asyncio
async def test_handle_client_authorizes_valid_handle_and_builds_backend():
    reader, writer, _transport = await _pipe_pair()
    cid = str(uuid.uuid4())
    reader.feed_data(wire.build_startup_message({"user": "kernel"}))
    reader.feed_data(wire.build_password_message("good"))
    reader.feed_eof()

    record = json.dumps(
        {"user": "alice", "company_id": cid, "audience": "sql", "read_only": True}
    )

    async def store_get(key):
        return record if key == f"{b._KEY_PREFIX}good" else None

    built = {}

    class _FakeBackend:
        def __init__(self, binding):
            built["binding"] = binding

        async def connect_privileged(self):
            built["connected"] = True

        async def execute(self, sql):
            built.setdefault("sql", []).append(sql)

        async def relay(self):
            built["relayed"] = True

        async def close(self):
            built["closed"] = True

    def backend_factory(binding, r, w):
        assert r is reader and w is writer  # client streams threaded through to the backend
        return _FakeBackend(binding)

    await server.handle_client(reader, writer, store_get, backend_factory)

    assert built["connected"] and built["relayed"] and built["closed"]
    assert built["binding"].company_id == cid
    # The tenant's read-only role was assumed before any relay.
    assert built["sql"][0] == f'SET ROLE "{b.reader_role(cid)}"'
