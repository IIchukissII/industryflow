# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``PostgresBackend``'s protocol behaviour, against a small fake Postgres over real sockets.

The live proof against a real database is test_sql_proxy_integration.py. What it cannot pin down as
cheaply — and what each of these would otherwise only show up as on a box — is:

  * **the session user is the tenant's own login**, never the enrolment principal, and a refused
    login is enrolled once and retried, never retried blindly;
  * **cancellation reaches the upstream session**, through a proxy-issued key that is not the
    upstream's own (the kernel never learns the database's backend key);
  * **a revoked capability ends a session that is already open** (ADR-0012 dec 6), instead of only
    being checked at connect.
"""
import asyncio
import os
import socket
import struct
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import binding as b  # noqa: E402
import server  # noqa: E402
import wire  # noqa: E402

UPSTREAM_KEY = (4242, 0x0BADBEEF)


class FakePostgres:
    """Enough of a Postgres to log a user in (trust auth), run simple queries, and take a cancel.

    ``refuse`` is the set of users whose login is refused with 28P01 until an enrolment statement
    for them has been run — the shape of a tenant login whose verifier is not installed yet.
    """

    def __init__(self, refuse=(), refuse_always=False):
        self.refuse = set(refuse)
        self.refuse_always = refuse_always
        self.logins = []
        self.queries = []
        self.cancels = []
        self.sessions = []
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self):
        for w in self.sessions:
            w.close()
        self._server.close()

    async def _handle(self, reader, writer):
        try:
            (length,) = struct.unpack("!I", await reader.readexactly(4))
            body = await reader.readexactly(length - 4)
            code, params = wire.parse_startup_packet(body)
            if code == wire.CANCEL_REQUEST_CODE:
                self.cancels.append(wire.parse_cancel_request(body))
                writer.close()
                return
            user = params["user"]
            self.logins.append(user)
            if user in self.refuse:
                writer.write(wire.build_error_response(
                    f'password authentication failed for user "{user}"', code="28P01"))
                await writer.drain()
                writer.close()
                return
            self.sessions.append(writer)
            writer.write(wire.build_authentication_ok())
            writer.write(wire.build_message(wire.MSG_PARAMETER_STATUS, b"server_version\x0015\x00"))
            writer.write(wire.build_backend_key_data(*UPSTREAM_KEY))
            writer.write(wire.build_ready_for_query())
            await writer.drain()
            while True:
                type_byte = await reader.readexactly(1)
                (length,) = struct.unpack("!I", await reader.readexactly(4))
                payload = await reader.readexactly(length - 4)
                if type_byte == wire.MSG_TERMINATE:
                    break
                if type_byte == wire.MSG_QUERY:
                    sql = payload[:-1].decode()
                    self.queries.append((user, sql))
                    if "notebook_sql_proxy_enrol" in sql and not self.refuse_always:
                        cid = sql.split("'")[1]
                        self.refuse.discard(b.tenant_login_role(cid))
                    writer.write(wire.build_message(b"C", b"SET\x00"))
                    writer.write(wire.build_ready_for_query())
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def _config(port):
    return server.UpstreamConfig(
        host="127.0.0.1", port=port, database="industryflow",
        enrol_user="notebook_sql_proxy", enrol_password="enrol-pw", tenant_key=b"k" * 32,
        ssl_context=None, require_tls=False,
    )


async def _kernel_pair():
    """The proxy's kernel-facing streams, and the kernel's end of the same socket."""
    proxy_sock, kernel_sock = socket.socketpair()
    p_reader, p_writer = await asyncio.open_connection(sock=proxy_sock)
    k_reader, k_writer = await asyncio.open_connection(sock=kernel_sock)
    return p_reader, p_writer, k_reader, k_writer


def _binding():
    return b.SqlBinding(user="alice", company_id=str(uuid.uuid4()))


# --- the session user ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_session_is_opened_as_the_tenants_own_login():
    pg = FakePostgres()
    cfg = _config(await pg.start())
    binding = _binding()
    p_reader, p_writer, _, _ = await _kernel_pair()
    backend = server.PostgresBackend(binding, p_reader, p_writer, cfg)
    try:
        await backend.connect()
    finally:
        await backend.close()
        await pg.stop()
    # Never the enrolment principal: that one may reach every tenant, and the kernel's bytes
    # would inherit whatever the session user may do.
    assert pg.logins == [b.tenant_login_role(binding.company_id)]


@pytest.mark.asyncio
async def test_a_refused_login_is_enrolled_once_then_retried():
    binding = _binding()
    login = b.tenant_login_role(binding.company_id)
    pg = FakePostgres(refuse={login})
    cfg = _config(await pg.start())
    p_reader, p_writer, _, _ = await _kernel_pair()
    backend = server.PostgresBackend(binding, p_reader, p_writer, cfg)
    try:
        await backend.connect()
    finally:
        await backend.close()
        await pg.stop()

    assert pg.logins == [login, "notebook_sql_proxy", login]
    (enrolled_by, sql), = pg.queries
    assert enrolled_by == "notebook_sql_proxy"
    assert sql.startswith(f"SELECT public.notebook_sql_proxy_enrol('{binding.company_id}'::uuid, 'SCRAM-SHA-256$")
    # The verifier, never the password, crosses to the database.
    assert b.tenant_login_password(cfg.tenant_key, binding.company_id) not in sql


@pytest.mark.asyncio
async def test_a_login_still_refused_after_enrolment_is_an_error_not_a_loop():
    binding = _binding()
    login = b.tenant_login_role(binding.company_id)
    pg = FakePostgres(refuse={login}, refuse_always=True)
    cfg = _config(await pg.start())
    p_reader, p_writer, _, _ = await _kernel_pair()
    backend = server.PostgresBackend(binding, p_reader, p_writer, cfg)
    try:
        with pytest.raises(server.UpstreamAuthRejected):
            await backend.connect()
    finally:
        await backend.close()
        await pg.stop()
    assert pg.logins == [login, "notebook_sql_proxy", login]


def test_tenant_passwords_are_per_tenant_and_keyed():
    a, c = str(uuid.uuid4()), str(uuid.uuid4())
    assert b.tenant_login_password(b"k" * 32, a) != b.tenant_login_password(b"k" * 32, c)
    assert b.tenant_login_password(b"k" * 32, a) != b.tenant_login_password(b"j" * 32, a)
    assert b.tenant_login_password(b"k" * 32, a) == b.tenant_login_password(b"k" * 32, a)
    with pytest.raises(ValueError):
        b.tenant_login_password(b"short", a)


# --- cancellation --------------------------------------------------------------------------------

async def _read_startup_reply(k_reader):
    """Read the kernel-side startup completion up to ReadyForQuery; return the BackendKeyData."""
    key = None
    while True:
        t = await k_reader.readexactly(1)
        (length,) = struct.unpack("!I", await k_reader.readexactly(4))
        payload = await k_reader.readexactly(length - 4)
        if t == wire.MSG_BACKEND_KEY_DATA:
            key = wire.parse_backend_key_data(payload)
        if t == wire.MSG_READY_FOR_QUERY:
            return key


@pytest.mark.asyncio
async def test_the_kernel_gets_a_proxy_key_that_cancels_its_upstream_session():
    pg = FakePostgres()
    cfg = _config(await pg.start())
    cancels = server.CancelRegistry(cfg)
    p_reader, p_writer, k_reader, k_writer = await _kernel_pair()
    backend = server.PostgresBackend(_binding(), p_reader, p_writer, cfg, cancels=cancels)
    await backend.connect()
    relay = asyncio.create_task(backend.relay())
    try:
        kernel_key = await _read_startup_reply(k_reader)
        assert kernel_key is not None
        assert kernel_key != UPSTREAM_KEY  # the database's own key never reaches the kernel

        # The kernel cancels the way libpq does: a fresh connection carrying a CancelRequest.
        c_reader, c_writer = await asyncio.open_connection(sock=_connected_to(cancels, cfg))
        c_writer.write(wire.build_cancel_request(*kernel_key))
        await c_writer.drain()
        await asyncio.wait_for(c_reader.read(), timeout=5)  # the proxy closes, replying nothing
        for _ in range(50):
            if pg.cancels:
                break
            await asyncio.sleep(0.02)
        assert pg.cancels == [UPSTREAM_KEY]
    finally:
        k_writer.close()
        await asyncio.wait_for(relay, timeout=5)
        await backend.close()
        await pg.stop()


@pytest.mark.asyncio
async def test_a_cancel_with_the_wrong_key_reaches_nothing():
    pg = FakePostgres()
    cfg = _config(await pg.start())
    cancels = server.CancelRegistry(cfg)
    pid, key = cancels.register(UPSTREAM_KEY)
    try:
        assert await cancels.cancel(pid, key ^ 1) is False
        assert await cancels.cancel(pid + 1, key) is False
        assert cancels.resolve(pid, key) == UPSTREAM_KEY
        cancels.unregister(pid)
        assert await cancels.cancel(pid, key) is False  # a finished session cancels nothing
    finally:
        await pg.stop()
    assert pg.cancels == []


def _connected_to(cancels, cfg):
    """A socket whose other end is served by handle_client — the proxy's listening path."""
    proxy_sock, kernel_sock = socket.socketpair()

    async def serve():
        reader, writer = await asyncio.open_connection(sock=proxy_sock)

        async def no_store(_key):
            return None

        await server.handle_client(reader, writer, no_store, lambda *a: None, cancels)

    asyncio.get_event_loop().create_task(serve())
    return kernel_sock


# --- revocation of an open session ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_revoked_capability_ends_the_open_session():
    pg = FakePostgres()
    cfg = _config(await pg.start())
    p_reader, p_writer, k_reader, k_writer = await _kernel_pair()
    backend = server.PostgresBackend(_binding(), p_reader, p_writer, cfg, revalidate_seconds=0.01)
    await backend.connect()

    answers = iter([True, True, False])

    async def still_authorized():
        return next(answers, False)

    try:
        relay = asyncio.create_task(backend.relay(still_authorized))
        await _read_startup_reply(k_reader)
        # The session works while the capability does...
        k_writer.write(wire.build_query("SELECT 1"))
        await k_writer.drain()
        # ...and is ended by the proxy once it stops resolving, with no kernel action at all.
        await asyncio.wait_for(relay, timeout=5)
        assert backend.revoked is True
        assert await asyncio.wait_for(_drain_to_eof(k_reader), timeout=5) is True
    finally:
        k_writer.close()
        await backend.close()
        await pg.stop()


@pytest.mark.asyncio
async def test_a_recheck_that_cannot_reach_the_store_ends_the_session():
    pg = FakePostgres()
    cfg = _config(await pg.start())
    p_reader, p_writer, k_reader, k_writer = await _kernel_pair()
    backend = server.PostgresBackend(_binding(), p_reader, p_writer, cfg, revalidate_seconds=0.01)
    await backend.connect()

    async def store_down():
        raise ConnectionError("store unreachable")

    try:
        relay = asyncio.create_task(backend.relay(store_down))
        await _read_startup_reply(k_reader)
        await asyncio.wait_for(relay, timeout=5)
        assert backend.revoked is True
    finally:
        k_writer.close()
        await backend.close()
        await pg.stop()


@pytest.mark.asyncio
async def test_a_session_whose_capability_holds_keeps_relaying():
    pg = FakePostgres()
    cfg = _config(await pg.start())
    p_reader, p_writer, k_reader, k_writer = await _kernel_pair()
    backend = server.PostgresBackend(_binding(), p_reader, p_writer, cfg, revalidate_seconds=0.01)
    await backend.connect()

    async def holds():
        return True

    relay = asyncio.create_task(backend.relay(holds))
    try:
        await _read_startup_reply(k_reader)
        await asyncio.sleep(0.1)  # several re-checks
        k_writer.write(wire.build_query("SELECT 1"))
        await k_writer.drain()
        await asyncio.wait_for(_read_startup_reply(k_reader), timeout=5)  # CommandComplete, ReadyForQuery
        assert backend.revoked is False
        assert (b.tenant_login_role(backend._binding.company_id), "SELECT 1") in pg.queries
    finally:
        k_writer.close()
        await asyncio.wait_for(relay, timeout=5)
        await backend.close()
        await pg.stop()


async def _drain_to_eof(reader):
    while await reader.read(65536):
        pass
    return True
