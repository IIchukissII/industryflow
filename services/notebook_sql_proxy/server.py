# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SQL access proxy server — the Postgres-wire entry point (ADR-0015 dec 5-6).

A notebook kernel connects with a normal Postgres driver, presenting its SQL-audience capability
handle in place of a password. This module:

  1. Speaks the Postgres frontend handshake to the kernel, asks for a cleartext password, and
     reads the handle (``read_startup`` / ``authenticate_client`` — pure protocol over the
     streams, unit-tested). A CancelRequest arriving instead is routed to ``CancelRegistry``.
  2. Hands the handle to ``proxy.serve_connection``, which resolves it to a tenant and, only if
     valid, opens the backend and assumes the tenant's read-only role (``binding.py`` policy),
     recording the outcome and the session into the capability-use record (ADR-0042).
  3. ``PostgresBackend`` opens the upstream connection as **the tenant's own login**
     (``tenant_sqlproxy_<uuid>``, SCRAM — the ADR-0017 method), enrolling that login's verifier
     first if the database does not yet accept it, runs the ``SET ROLE`` setup, completes the
     kernel handshake, and relays bytes both ways until either side closes or the handle is
     revoked.

The relay is byte-transparent, which is why the upstream session user must itself be confined to
the tenant: whatever that user may do, the kernel may do. See binding.py and
13-notebook-sql-proxy-role.sh.

Steps 1-2, the wire codec (``wire.py``) and the SCRAM core (``scram.py``) are pure and
unit-tested; ``PostgresBackend`` is exercised against a real Postgres by
``tests/test_sql_proxy_integration.py`` in the db-tenant-isolation workflow.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import secrets
import ssl
import struct
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import binding as policy
import wire
from binding import SqlBinding
from proxy import StillAuthorized, serve_connection

log = logging.getLogger("notebook_sql_proxy")

# Upstream TLS modes (a subset of libpq's sslmode). The default is verify-full, matching the
# hardened DB ADR-0017 enforces: encrypt AND verify the server cert against the internal CA AND
# check the hostname. "require" encrypts without verifying; "disable" is plaintext (dev only).
_TLS_REQUIRED = {"require", "verify-ca", "verify-full"}

# SQLSTATEs with which Postgres refuses an authentication: a wrong password (28P01), or no
# pg_hba entry — which is what a role that does not exist yet gets from a group-scoped rule (28000).
_AUTH_REJECTED = {"28P01", "28000"}


class UpstreamAuthRejected(ConnectionError):
    """The database refused the login itself (as opposed to the network or the protocol)."""


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


@dataclass(frozen=True)
class ClientStartup:
    """The kernel's opening packet: a StartupMessage, or a CancelRequest for a running session."""

    code: int
    params: Dict[str, str]
    body: bytes

    @property
    def is_cancel(self) -> bool:
        return self.code == wire.CANCEL_REQUEST_CODE


async def read_startup(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> ClientStartup:
    """Read the kernel's opening packet, declining an optional SSLRequest/GSSENCRequest first."""
    body = await _read_startup_packet(reader)
    code, params = wire.parse_startup_packet(body)
    while code in (wire.SSL_REQUEST_CODE, wire.GSSENC_REQUEST_CODE):
        writer.write(_DECLINE_SSL)
        await writer.drain()
        body = await _read_startup_packet(reader)
        code, params = wire.parse_startup_packet(body)
    return ClientStartup(code=code, params=params, body=body)


async def request_handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> str:
    """Request a cleartext password and return it — the kernel's capability handle."""
    writer.write(wire.build_authentication_cleartext_password())
    await writer.drain()

    type_byte, payload = await _read_regular_message(reader)
    if type_byte != wire.MSG_PASSWORD:
        raise wire.ProtocolError(f"expected PasswordMessage, got {type_byte!r}")
    return wire.parse_password_message(payload)


async def authenticate_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> Tuple[Dict[str, str], str]:
    """Run the frontend handshake and return ``(startup_params, capability_handle)``.

    Declines an optional SSLRequest/GSSENCRequest, reads the StartupMessage, requests a cleartext
    password, and reads the PasswordMessage — whose value is the kernel's capability handle.
    Pure protocol over the streams: unit-testable with in-memory pipes, no database involved.
    """
    startup = await read_startup(reader, writer)
    if startup.is_cancel:
        raise wire.ProtocolError("a CancelRequest is not a session")
    return startup.params, await request_handle(reader, writer)


async def reject_client(writer: asyncio.StreamWriter, message: str) -> None:
    """Send a FATAL ErrorResponse to a kernel whose handle was refused, then close."""
    writer.write(wire.build_error_response(message))
    with _suppress():
        await writer.drain()
    writer.close()


# --------------------------------------------------------------------- upstream

@dataclass(frozen=True)
class UpstreamConfig:
    """Where and how the proxy reaches the database, and the two credentials it holds.

    ``enrol_user``/``enrol_password`` are the enrolment principal (``notebook_sql_proxy``): a member
    of nothing, able only to install a tenant login's verifier. No kernel byte ever reaches a
    session opened as it. ``tenant_key`` derives every tenant login's password; the database never
    sees it.
    """

    host: str
    port: int
    database: str
    enrol_user: str
    enrol_password: str
    tenant_key: bytes
    ssl_context: Optional[ssl.SSLContext] = None
    require_tls: bool = True


class UpstreamConnection:
    """One authenticated upstream session: TLS, StartupMessage, SCRAM, then ReadyForQuery."""

    def __init__(self, config: UpstreamConfig) -> None:
        self._config = config
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        # Upstream ParameterStatus messages, buffered during startup and replayed to the kernel
        # *after* AuthenticationOk — a client expects them only once it is authenticated.
        self.startup_params = bytearray()
        # The upstream backend's (pid, key) — what a CancelRequest must quote. Never shown to the
        # kernel: the kernel gets a proxy-issued pair that maps to it (CancelRegistry).
        self.backend_key: Optional[Tuple[int, int]] = None

    async def open(self, user: str, password: str) -> None:
        # TLS is negotiated (SSLRequest) before the StartupMessage, so credentials and the
        # subsequent SCRAM exchange never cross the network in the clear (ADR-0017).
        cfg = self._config
        self.reader, self.writer = await open_upstream(
            cfg.host, cfg.port, cfg.ssl_context, require_tls=cfg.require_tls
        )
        self.writer.write(wire.build_startup_message({"user": user, "database": cfg.database}))
        await self.writer.drain()
        await self._authenticate(user, password)
        await self._drain_until_ready()

    async def _read(self) -> Tuple[bytes, bytes]:
        """Read one message; an ErrorResponse becomes an exception, a refused login a typed one."""
        type_byte, payload = await _read_regular_message(self.reader)
        if type_byte == wire.MSG_ERROR_RESPONSE:
            fields = wire.parse_error_response(payload)
            message = fields.get("M", "upstream error")
            if fields.get("C") in _AUTH_REJECTED:
                raise UpstreamAuthRejected(message)
            raise ConnectionError(message)
        return type_byte, payload

    async def _authenticate(self, user: str, password: str) -> None:
        from scram import ScramClient  # local import: scram is runtime-only here

        type_byte, payload = await self._read()
        if type_byte != wire.MSG_AUTHENTICATION:
            raise wire.ProtocolError("expected Authentication from upstream")
        sub, _rest = wire.parse_authentication(payload)
        if sub == wire.AUTH_OK:
            return
        if sub != wire.AUTH_SASL:
            raise wire.ProtocolError(f"unsupported upstream auth code {sub} (expected SCRAM)")

        client = ScramClient(user, password, secrets.token_urlsafe(18))
        first = client.client_first()
        self.writer.write(
            wire.build_message(b"p", b"SCRAM-SHA-256\x00" + struct.pack("!I", len(first)) + first)
        )
        await self.writer.drain()

        _, payload = await self._read()
        _, server_first = wire.parse_authentication(payload)
        final = client.client_final(server_first)
        self.writer.write(wire.build_message(b"p", final))
        await self.writer.drain()

        # A wrong password is refused HERE, as an ErrorResponse in place of SASLFinal.
        _, payload = await self._read()
        _, server_final = wire.parse_authentication(payload)
        if not client.verify_server_final(server_final):
            raise ConnectionError("upstream SCRAM server signature verification failed")
        _, payload = await self._read()  # AuthenticationOk
        sub, _ = wire.parse_authentication(payload)
        if sub != wire.AUTH_OK:
            raise wire.ProtocolError("upstream did not complete SCRAM with AuthenticationOk")

    async def _drain_until_ready(self) -> None:
        while True:
            type_byte, payload = await self._read()
            if type_byte == wire.MSG_PARAMETER_STATUS:
                # Buffer — NOT forward. A ParameterStatus is itself a type-'S' message, and the
                # kernel is not yet authenticated (AuthenticationOk is sent in relay()); forwarding
                # it now makes the client read 'S' where it expects the auth reply. Replayed later.
                self.startup_params += wire.build_message(type_byte, payload)
            elif type_byte == wire.MSG_BACKEND_KEY_DATA:
                self.backend_key = wire.parse_backend_key_data(payload)
            elif type_byte == wire.MSG_READY_FOR_QUERY:
                return

    async def execute(self, sql: str) -> None:
        """Run one simple-protocol statement and consume its completion."""
        self.writer.write(wire.build_query(sql))
        await self.writer.drain()
        error: Optional[str] = None
        while True:
            type_byte, payload = await _read_regular_message(self.reader)
            if type_byte == wire.MSG_ERROR_RESPONSE and error is None:
                error = wire.parse_error_response(payload).get("M", sql)
            if type_byte == wire.MSG_READY_FOR_QUERY:
                if error is not None:
                    raise ConnectionError(f"statement failed: {error}")
                return

    async def close(self) -> None:
        if self.writer is not None and not self.writer.is_closing():
            with _suppress():
                self.writer.write(wire.build_message(wire.MSG_TERMINATE))
                self.writer.close()


async def enrol_tenant_login(config: UpstreamConfig, company_id: str, password: str) -> None:
    """Install the tenant login's SCRAM verifier, through the enrolment principal.

    A short session of its own — one statement, then closed — so the one principal with
    cross-tenant authority never carries a kernel's bytes.
    """
    from scram import scram_verifier  # local import: scram is runtime-only here

    statement = policy.enrol_statement(company_id, scram_verifier(password))
    conn = UpstreamConnection(config)
    try:
        await conn.open(config.enrol_user, config.enrol_password)
        await conn.execute(statement)
    finally:
        await conn.close()


# --------------------------------------------------------------------- cancellation

class CancelRegistry:
    """Maps the (pid, key) the proxy gives each kernel to the upstream session it relays to.

    A kernel interrupts a query by opening a new connection and sending a CancelRequest quoting the
    BackendKeyData it was given. The proxy never shows the kernel the upstream's own key — it issues
    a pair of its own per session — so a cancel can only ever reach the session it was issued for,
    and only through the proxy. Process-local: the deployment runs one proxy replica.
    """

    def __init__(self, config: UpstreamConfig) -> None:
        self._config = config
        self._sessions: Dict[int, Tuple[int, Tuple[int, int]]] = {}

    def register(self, upstream_key: Tuple[int, int]) -> Tuple[int, int]:
        pid = secrets.randbits(31) or 1
        while pid in self._sessions:
            pid = secrets.randbits(31) or 1
        key = secrets.randbits(32)
        self._sessions[pid] = (key, upstream_key)
        return pid, key

    def unregister(self, pid: int) -> None:
        self._sessions.pop(pid, None)

    def resolve(self, pid: int, key: int) -> Optional[Tuple[int, int]]:
        """The upstream (pid, key) a kernel's cancel maps to, or None for an unknown/wrong key."""
        entry = self._sessions.get(pid)
        if entry is None:
            return None
        expected, upstream = entry
        if not hmac.compare_digest(struct.pack("!I", expected), struct.pack("!I", key)):
            return None
        return upstream

    async def cancel(self, pid: int, key: int) -> bool:
        """Forward a kernel's CancelRequest upstream. Returns whether one was forwarded."""
        upstream = self.resolve(pid, key)
        if upstream is None:
            return False
        cfg = self._config
        reader, writer = await open_upstream(
            cfg.host, cfg.port, cfg.ssl_context, require_tls=cfg.require_tls
        )
        try:
            writer.write(wire.build_cancel_request(*upstream))
            await writer.drain()
        finally:
            with _suppress():
                writer.close()
        return True


# --------------------------------------------------------------------- backend

class PostgresBackend:
    """Live upstream session for one kernel: tenant login, SET ROLE, relay, revocation, cancel."""

    def __init__(
        self,
        binding: SqlBinding,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        config: UpstreamConfig,
        *,
        cancels: Optional[CancelRegistry] = None,
        revalidate_seconds: float = 15.0,
    ) -> None:
        self._binding = binding
        self._client_reader = client_reader
        self._client_writer = client_writer
        self._config = config
        self._cancels = cancels
        self._revalidate_seconds = revalidate_seconds
        self._upstream = UpstreamConnection(config)
        self.revoked = False

    async def connect(self) -> None:
        """Open the session as the tenant's own login, enrolling it once if the database refuses.

        The login is refused when its verifier was never installed (a tenant provisioned since, or a
        fresh database) or was installed from another key (the key rotated). Either way the remedy
        is the same, and is attempted exactly once: anything still refused after it is an error.
        """
        cid = self._binding.company_id
        user = policy.tenant_login_role(cid)
        password = policy.tenant_login_password(self._config.tenant_key, cid)
        try:
            await self._upstream.open(user, password)
        except UpstreamAuthRejected:
            await self._upstream.close()
            await enrol_tenant_login(self._config, cid, password)
            self._upstream = UpstreamConnection(self._config)
            await self._upstream.open(user, password)

    async def execute(self, sql: str) -> None:
        """Run one setup statement (SET ROLE / search_path / read-only)."""
        await self._upstream.execute(sql)

    async def relay(self, still_authorized: Optional[StillAuthorized] = None) -> None:
        """Complete the kernel handshake, then pipe bytes both ways until either side closes or the
        capability stops resolving."""
        cancel_pid: Optional[int] = None
        # The kernel sees a normal startup completion: AuthenticationOk, the upstream's
        # ParameterStatus messages (buffered during connect), the proxy's own BackendKeyData, then
        # ReadyForQuery. Only after this is the kernel authenticated and ready to send queries.
        self._client_writer.write(wire.build_authentication_ok())
        if self._upstream.startup_params:
            self._client_writer.write(bytes(self._upstream.startup_params))
        if self._cancels is not None and self._upstream.backend_key is not None:
            cancel_pid, cancel_key = self._cancels.register(self._upstream.backend_key)
            self._client_writer.write(wire.build_backend_key_data(cancel_pid, cancel_key))
        self._client_writer.write(wire.build_ready_for_query())
        await self._client_writer.drain()

        watchdog = None
        if still_authorized is not None:
            watchdog = asyncio.create_task(self._watch(still_authorized))
        try:
            await asyncio.gather(
                _pipe(self._client_reader, self._upstream.writer),
                _pipe(self._upstream.reader, self._client_writer),
            )
        finally:
            if watchdog is not None:
                watchdog.cancel()
                await asyncio.gather(watchdog, return_exceptions=True)
            if cancel_pid is not None:
                self._cancels.unregister(cancel_pid)

    async def _watch(self, still_authorized: StillAuthorized) -> None:
        """Re-check the capability for the life of the session; end the session once it fails.

        A store that cannot answer is treated as a refusal, exactly as at connect: the lookup is the
        authorization, and an authorization that cannot be confirmed is not one.
        """
        while True:
            await asyncio.sleep(self._revalidate_seconds)
            try:
                ok = await still_authorized()
            except Exception:  # noqa: BLE001 - any failure to confirm is a refusal
                log.exception("capability re-check failed; ending the session")
                ok = False
            if not ok:
                self.revoked = True
                # Closing both sockets ends both pipes. The relay is byte-transparent, so there is no
                # message boundary to place a polite ErrorResponse at; the kernel sees the server
                # close the connection, which is what happened.
                await self.close()
                return

    async def close(self) -> None:
        await self._upstream.close()
        if not self._client_writer.is_closing():
            with _suppress():
                self._client_writer.close()


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
    store,
    backend_factory,
    cancels: Optional[CancelRegistry] = None,
) -> None:
    """Authenticate one kernel and, if its handle is valid, bind it to its tenant and relay.

    ``store`` is the capability store (``proxy.CapabilityStore``): the handle is resolved against it
    and the outcome recorded into it (ADR-0042). A CancelRequest is answered the way Postgres
    answers one: forwarded if it names a live session with the right key, and the connection closed
    with no reply either way; it presents no capability and is not recorded.
    """
    try:
        startup = await read_startup(reader, writer)
        if startup.is_cancel:
            pid, key = wire.parse_cancel_request(startup.body)
            if cancels is not None:
                with _suppress():
                    await cancels.cancel(pid, key)
            writer.close()
            return
        handle = await request_handle(reader, writer)
    except (asyncio.IncompleteReadError, wire.ProtocolError):
        writer.close()
        return

    def factory(binding: SqlBinding):
        return backend_factory(binding, reader, writer)

    peer = writer.get_extra_info("peername")
    served = await serve_connection(handle, store, factory,
                                    source=peer[0] if isinstance(peer, (tuple, list)) and peer else None)
    if not served:
        await reject_client(writer, "capability handle refused")


def _tenant_key_from_env() -> bytes:
    key = os.environ.get("SQL_PROXY_TENANT_LOGIN_KEY", "").encode("utf-8")
    if len(key) < policy.MIN_TENANT_KEY_BYTES:
        raise SystemExit(
            f"SQL_PROXY_TENANT_LOGIN_KEY must be set to at least {policy.MIN_TENANT_KEY_BYTES} "
            "characters (it derives every tenant's SQL login; generate: openssl rand -hex 32)"
        )
    return key


async def main() -> None:  # pragma: no cover - cluster-bound entry point
    import redis.asyncio as aioredis

    logging.basicConfig(level=logging.INFO)
    listen_host = os.environ.get("SQL_PROXY_HOST", "0.0.0.0")
    listen_port = int(os.environ.get("SQL_PROXY_PORT", "6432"))
    redis_url = os.environ["SQL_PROXY_REDIS_URL"]
    # How often an open session re-checks its capability (ADR-0012 dec 6): the longest a revoked
    # handle keeps a session it already opened.
    revalidate_seconds = float(os.environ.get("SQL_PROXY_REVALIDATE_SECONDS", "15"))
    # Upstream TLS (ADR-0017): verify-full by default, against the internal CA mounted as the
    # standard DB client root. Set SQL_PROXY_DB_SSLMODE=disable only for a dev DB without certs.
    sslmode = os.environ.get("SQL_PROXY_DB_SSLMODE", "verify-full")
    config = UpstreamConfig(
        host=os.environ["SQL_PROXY_DB_HOST"],
        port=int(os.environ.get("SQL_PROXY_DB_PORT", "5432")),
        database=os.environ.get("SQL_PROXY_DB_NAME", "industryflow"),
        # The enrolment principal (13-notebook-sql-proxy-role.sh): a member of nothing.
        enrol_user=os.environ["SQL_PROXY_DB_USER"],
        enrol_password=os.environ["SQL_PROXY_DB_PASSWORD"],
        tenant_key=_tenant_key_from_env(),
        ssl_context=build_upstream_ssl_context(sslmode, os.environ.get("SQL_PROXY_DB_SSLROOTCERT")),
        require_tls=sslmode in _TLS_REQUIRED,
    )
    store = aioredis.from_url(redis_url)
    cancels = CancelRegistry(config)

    def backend_factory(binding, client_reader, client_writer):
        return PostgresBackend(
            binding, client_reader, client_writer, config,
            cancels=cancels, revalidate_seconds=revalidate_seconds,
        )

    async def on_connect(reader, writer):
        await handle_client(reader, writer, store, backend_factory, cancels)

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
