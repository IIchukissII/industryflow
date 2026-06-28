# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SQL access proxy server — the Postgres-wire entry point (ADR-0015 dec 5-6).

A notebook kernel connects with a normal Postgres driver, presenting its SQL-audience capability
handle in place of a password. This module:

  1. Speaks the Postgres frontend handshake to the kernel, asks for a cleartext password, and
     reads the handle (``authenticate_client`` — pure protocol over the streams, unit-tested).
  2. Hands the handle to ``proxy.serve_connection``, which resolves it to a tenant and, only if
     valid, opens the backend and assumes the tenant's read-only role (``binding.py`` policy).
  3. ``PostgresBackend`` opens the upstream connection as the privileged principal (SCRAM, the
     ADR-0017 method), runs the ``SET ROLE`` setup, completes the kernel handshake, and relays
     bytes both ways until either side closes.

Boundary (per README): steps 1-2 and the wire codec (``wire.py``) and SCRAM core (``scram.py``)
are pure/unit-tested. ``PostgresBackend`` — the live upstream connection and byte relay — is the
cluster-bound adapter; it needs the integration test against a real Postgres tracked in issue #19
and is labelled *not cluster-validated*.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import ssl
import struct
from typing import Dict, Optional, Tuple

import wire
from binding import SqlBinding
from proxy import serve_connection

# Upstream TLS modes (a subset of libpq's sslmode). The default is verify-full, matching the
# hardened DB ADR-0017 enforces: encrypt AND verify the server cert against the internal CA AND
# check the hostname. "require" encrypts without verifying; "disable" is plaintext (dev only).
_TLS_REQUIRED = {"require", "verify-ca", "verify-full"}


def build_upstream_ssl_context(sslmode: str, sslrootcert: Optional[str]) -> Optional[ssl.SSLContext]:
    """Build the SSLContext for the proxy → TimescaleDB hop, or None for ``sslmode=disable``.

    verify-full/verify-ca verify the server certificate against ``sslrootcert`` (the internal CA,
    ADR-0017); verify-full additionally checks the hostname. require encrypts only. Unknown modes
    are treated as verify-full (fail-safe toward stronger verification).
    """
    if sslmode == "disable":
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if sslmode == "require":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    # verify-ca / verify-full (default): trust only the supplied CA.
    ctx.check_hostname = sslmode != "verify-ca"
    ctx.verify_mode = ssl.CERT_REQUIRED
    if sslrootcert:
        ctx.load_verify_locations(cafile=sslrootcert)
    return ctx


async def open_upstream(
    host: str,
    port: int,
    ssl_context: Optional[ssl.SSLContext],
    *,
    require_tls: bool,
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open the upstream connection, negotiating Postgres TLS when a context is supplied.

    Postgres TLS is in-band: the client sends an SSLRequest, the server replies a single byte —
    ``S`` (willing) or ``N`` (not) — and only on ``S`` is the socket upgraded *before* the
    StartupMessage. If the server declines and TLS is required (verify-*/require), we refuse
    rather than fall back to plaintext.
    """
    reader, writer = await asyncio.open_connection(host, port)
    if ssl_context is None:
        return reader, writer
    try:
        writer.write(wire.build_ssl_request())
        await writer.drain()
        response = await reader.readexactly(1)
        if response == b"S":
            await _upgrade_to_tls(reader, writer, ssl_context, server_hostname=host)
            return reader, writer
        if response == b"N":
            if require_tls:
                raise ConnectionError("upstream refused TLS but sslmode requires it")
            return reader, writer
        raise wire.ProtocolError(f"unexpected SSLRequest reply: {response!r}")
    except BaseException:
        # Never leak the raw socket on a failed negotiation (a refused-TLS error, a handshake
        # failure, or cancellation) — close it before propagating.
        with _suppress():
            writer.close()
        raise


async def _upgrade_to_tls(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ssl_context: ssl.SSLContext,
    *,
    server_hostname: str,
) -> None:
    """Upgrade an open StreamReader/Writer pair to TLS in place (asyncio start_tls)."""
    loop = asyncio.get_event_loop()
    transport = writer.transport
    protocol = transport.get_protocol()
    new_transport = await loop.start_tls(
        transport, protocol, ssl_context, server_hostname=server_hostname
    )
    # start_tls keeps feeding the same StreamReader through `protocol`; repoint the writer at the
    # TLS transport so subsequent writes are encrypted.
    writer._transport = new_transport  # noqa: SLF001 - the documented asyncio upgrade pattern
    reader._transport = new_transport  # noqa: SLF001

# The kernel↔proxy hop is pod-internal (the proxy sits beside the hub on the pod network / the
# compose bridge); TLS for it is the deployment's NetworkPolicy/edge concern, not negotiated here.
# We therefore decline the kernel's optional SSLRequest and proceed in cleartext on that hop.
_DECLINE_SSL = b"N"


# --------------------------------------------------------------------- frontend handshake (pure)

async def _read_startup_packet(reader: asyncio.StreamReader) -> bytes:
    """Read one startup-phase packet body (the bytes after the Int32 length prefix)."""
    header = await reader.readexactly(4)
    (length,) = struct.unpack("!I", header)
    if length < 4 or length > 1 << 20:
        raise wire.ProtocolError(f"implausible startup length: {length}")
    return await reader.readexactly(length - 4)


async def _read_regular_message(reader: asyncio.StreamReader) -> Tuple[bytes, bytes]:
    """Read one typed message, returning ``(type_byte, payload)``."""
    type_byte = await reader.readexactly(1)
    (length,) = struct.unpack("!I", await reader.readexactly(4))
    if length < 4 or length > 1 << 24:
        raise wire.ProtocolError(f"implausible message length: {length}")
    return type_byte, await reader.readexactly(length - 4)


async def authenticate_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> Tuple[Dict[str, str], str]:
    """Run the frontend handshake and return ``(startup_params, capability_handle)``.

    Declines an optional SSLRequest/GSSENCRequest, reads the StartupMessage, requests a cleartext
    password, and reads the PasswordMessage — whose value is the kernel's capability handle.
    Pure protocol over the streams: unit-testable with in-memory pipes, no database involved.
    """
    body = await _read_startup_packet(reader)
    code, params = wire.parse_startup_packet(body)
    while code in (wire.SSL_REQUEST_CODE, wire.GSSENC_REQUEST_CODE):
        writer.write(_DECLINE_SSL)
        await writer.drain()
        body = await _read_startup_packet(reader)
        code, params = wire.parse_startup_packet(body)

    writer.write(wire.build_authentication_cleartext_password())
    await writer.drain()

    type_byte, payload = await _read_regular_message(reader)
    if type_byte != wire.MSG_PASSWORD:
        raise wire.ProtocolError(f"expected PasswordMessage, got {type_byte!r}")
    handle = wire.parse_password_message(payload)
    return params, handle


async def reject_client(writer: asyncio.StreamWriter, message: str) -> None:
    """Send a FATAL ErrorResponse to a kernel whose handle was refused, then close."""
    writer.write(wire.build_error_response(message))
    with _suppress():
        await writer.drain()
    writer.close()


# --------------------------------------------------------------------- backend (cluster-bound)

class PostgresBackend:
    """Live upstream session: connect as the privileged principal, SET ROLE, relay.

    NOT cluster-validated — needs the integration test against a real Postgres (issue #19). The
    privileged login role is a member of every ``tenant_reader_<uuid>`` with ``NOINHERIT`` so it
    holds no tenant privilege until it explicitly ``SET ROLE``s (ADR-0015 dec 5).
    """

    def __init__(
        self,
        binding: SqlBinding,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        ssl_context: Optional[ssl.SSLContext] = None,
        require_tls: bool = True,
    ) -> None:
        self._binding = binding
        self._client_reader = client_reader
        self._client_writer = client_writer
        self._host, self._port = host, port
        self._user, self._password, self._database = user, password, database
        self._ssl_context = ssl_context
        self._require_tls = require_tls
        self._up_reader: Optional[asyncio.StreamReader] = None
        self._up_writer: Optional[asyncio.StreamWriter] = None
        self._params: Dict[str, bytes] = {}

    async def connect_privileged(self) -> None:
        # TLS is negotiated (SSLRequest) before the StartupMessage, so credentials and the
        # subsequent SCRAM exchange never cross the network in the clear (ADR-0017).
        self._up_reader, self._up_writer = await open_upstream(
            self._host, self._port, self._ssl_context, require_tls=self._require_tls
        )
        self._up_writer.write(
            wire.build_startup_message({"user": self._user, "database": self._database})
        )
        await self._up_writer.drain()
        await self._authenticate_upstream()
        await self._drain_until_ready()  # consume ParameterStatus/BackendKeyData/ReadyForQuery

    async def _authenticate_upstream(self) -> None:
        from scram import ScramClient  # local import: scram is runtime-only here

        type_byte, payload = await _read_regular_message(self._up_reader)
        if type_byte == wire.MSG_ERROR_RESPONSE:
            raise ConnectionError(wire.parse_error_response(payload).get("M", "upstream error"))
        if type_byte != wire.MSG_AUTHENTICATION:
            raise wire.ProtocolError("expected Authentication from upstream")
        sub, _rest = wire.parse_authentication(payload)
        if sub == wire.AUTH_OK:
            return
        if sub != wire.AUTH_SASL:
            raise wire.ProtocolError(f"unsupported upstream auth code {sub} (expected SCRAM)")

        client = ScramClient(self._user, self._password, secrets.token_urlsafe(18))
        first = client.client_first()
        self._up_writer.write(
            wire.build_message(b"p", b"SCRAM-SHA-256\x00" + struct.pack("!I", len(first)) + first)
        )
        await self._up_writer.drain()

        _, payload = await _read_regular_message(self._up_reader)
        _, server_first = wire.parse_authentication(payload)
        final = client.client_final(server_first)
        self._up_writer.write(wire.build_message(b"p", final))
        await self._up_writer.drain()

        _, payload = await _read_regular_message(self._up_reader)
        _, server_final = wire.parse_authentication(payload)
        if not client.verify_server_final(server_final):
            raise ConnectionError("upstream SCRAM server signature verification failed")
        _, payload = await _read_regular_message(self._up_reader)  # AuthenticationOk
        sub, _ = wire.parse_authentication(payload)
        if sub != wire.AUTH_OK:
            raise wire.ProtocolError("upstream did not complete SCRAM with AuthenticationOk")

    async def _drain_until_ready(self) -> None:
        while True:
            type_byte, payload = await _read_regular_message(self._up_reader)
            if type_byte == wire.MSG_PARAMETER_STATUS:
                self._params.setdefault(b"_order", b"")  # preserve receipt; forwarded verbatim
                self._forward_to_client(wire.build_message(type_byte, payload))
            elif type_byte == wire.MSG_BACKEND_KEY_DATA:
                continue  # the proxy owns cancellation; do not expose upstream keys to the kernel
            elif type_byte == wire.MSG_READY_FOR_QUERY:
                return
            elif type_byte == wire.MSG_ERROR_RESPONSE:
                raise ConnectionError(wire.parse_error_response(payload).get("M", "upstream error"))

    def _forward_to_client(self, data: bytes) -> None:
        self._client_writer.write(data)

    async def execute(self, sql: str) -> None:
        """Run one setup statement (SET ROLE / read-only) and consume its completion."""
        self._up_writer.write(wire.build_query(sql))
        await self._up_writer.drain()
        while True:
            type_byte, payload = await _read_regular_message(self._up_reader)
            if type_byte == wire.MSG_ERROR_RESPONSE:
                raise ConnectionError(
                    f"setup statement failed: {wire.parse_error_response(payload).get('M', sql)}"
                )
            if type_byte == wire.MSG_READY_FOR_QUERY:
                return

    async def relay(self) -> None:
        """Complete the kernel handshake, then pipe bytes both ways until either side closes."""
        # Tell the kernel it is authenticated and ready. ParameterStatus messages were already
        # forwarded during upstream startup; we issue our own BackendKeyData-free ReadyForQuery.
        self._client_writer.write(wire.build_authentication_ok())
        self._client_writer.write(wire.build_ready_for_query())
        await self._client_writer.drain()

        await asyncio.gather(
            _pipe(self._client_reader, self._up_writer),
            _pipe(self._up_reader, self._client_writer),
        )

    async def close(self) -> None:
        for w in (self._up_writer, self._client_writer):
            if w is not None and not w.is_closing():
                with _suppress():
                    w.close()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes from ``reader`` to ``writer`` until EOF, then half-close the writer."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        with _suppress():
            writer.close()


# --------------------------------------------------------------------- connection handler / main

async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    store_get,
    backend_factory,
) -> None:
    """Authenticate one kernel and, if its handle is valid, bind it to its tenant and relay."""
    try:
        _params, handle = await authenticate_client(reader, writer)
    except (asyncio.IncompleteReadError, wire.ProtocolError):
        writer.close()
        return

    def factory(binding: SqlBinding):
        return backend_factory(binding, reader, writer)

    served = await serve_connection(handle, store_get, factory)
    if not served:
        await reject_client(writer, "capability handle refused")


async def main() -> None:  # pragma: no cover - cluster-bound entry point
    import redis.asyncio as aioredis

    listen_host = os.environ.get("SQL_PROXY_HOST", "0.0.0.0")
    listen_port = int(os.environ.get("SQL_PROXY_PORT", "6432"))
    redis_url = os.environ["SQL_PROXY_REDIS_URL"]
    db = dict(
        host=os.environ["SQL_PROXY_DB_HOST"],
        port=int(os.environ.get("SQL_PROXY_DB_PORT", "5432")),
        user=os.environ["SQL_PROXY_DB_USER"],
        password=os.environ["SQL_PROXY_DB_PASSWORD"],
        database=os.environ.get("SQL_PROXY_DB_NAME", "industryflow"),
    )
    # Upstream TLS (ADR-0017): verify-full by default, against the internal CA mounted as the
    # standard DB client root. Set SQL_PROXY_DB_SSLMODE=disable only for a dev DB without certs.
    sslmode = os.environ.get("SQL_PROXY_DB_SSLMODE", "verify-full")
    ssl_context = build_upstream_ssl_context(sslmode, os.environ.get("SQL_PROXY_DB_SSLROOTCERT"))
    require_tls = sslmode in _TLS_REQUIRED
    store = aioredis.from_url(redis_url)

    def backend_factory(binding, client_reader, client_writer):
        return PostgresBackend(
            binding, client_reader, client_writer,
            ssl_context=ssl_context, require_tls=require_tls, **db,
        )

    async def on_connect(reader, writer):
        await handle_client(reader, writer, store.get, backend_factory)

    server = await asyncio.start_server(on_connect, listen_host, listen_port)
    async with server:
        await server.serve_forever()


class _suppress:
    """Tiny context manager: ignore errors from best-effort socket teardown."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
