# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the SQL proxy connection orchestration (ADR-0015), with a fake backend/store."""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import binding as b  # noqa: E402
import proxy  # noqa: E402
from capability_audit_fake import AuditScripts  # noqa: E402
from industryflow_contracts import capability_audit as audit  # noqa: E402


class FakeAsyncStore(AuditScripts):
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    async def get(self, key):
        return self.mapping.get(key)

    async def delete(self, *keys):
        return sum(self.mapping.pop(k, None) is not None for k in keys)


class FakeBackend:
    def __init__(self):
        self.connected = False
        self.executed: list[str] = []
        self.relayed = False
        self.closed = False
        self.still_authorized = None

    async def connect(self):
        self.connected = True

    async def execute(self, sql):
        self.executed.append(sql)

    async def relay(self, still_authorized):
        self.relayed = True
        self.still_authorized = still_authorized

    async def close(self):
        self.closed = True


def _sql_record(company_id, handle_id=None, audience="sql"):
    record = {"user": "alice", "company_id": company_id, "audience": audience, "read_only": True}
    if handle_id is not None:
        record["handle_id"] = handle_id
    return json.dumps(record)


@pytest.mark.asyncio
async def test_valid_handle_binds_tenant_and_relays():
    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(cid)})
    backends = []

    def factory(binding):
        be = FakeBackend()
        backends.append(be)
        return be

    ok = await proxy.serve_connection("good", store, factory)

    assert ok is True
    assert len(backends) == 1
    be = backends[0]
    assert be.connected and be.relayed and be.closed
    # The connection assumed the tenant's read-only role before relaying.
    assert be.executed[0] == f'SET ROLE "{b.reader_role(cid)}"'
    assert any("read_only" in s for s in be.executed)


@pytest.mark.asyncio
async def test_invalid_handle_opens_no_backend():
    store = FakeAsyncStore({})  # nothing minted
    opened = []

    def factory(binding):
        opened.append(binding)
        return FakeBackend()

    ok = await proxy.serve_connection("nope", store, factory)

    assert ok is False
    # The privileged principal is never used for an unresolved handle.
    assert opened == []


@pytest.mark.asyncio
async def test_api_handle_is_rejected_by_sql_proxy():
    cid = str(uuid.uuid4())
    api_rec = json.dumps({"user": "a", "company_id": cid, "audience": "api", "read_only": True})
    store = FakeAsyncStore({f"{b._KEY_PREFIX}apionly": api_rec})

    ok = await proxy.serve_connection("apionly", store, lambda binding: FakeBackend())
    assert ok is False


# --- the session re-checks its capability for as long as it lives (ADR-0012 dec 6) -------------

async def _served(store, handle="good"):
    backends = []

    def factory(binding):
        be = FakeBackend()
        backends.append(be)
        return be

    assert await proxy.serve_connection(handle, store, factory) is True
    return backends[0]


@pytest.mark.asyncio
async def test_the_relay_is_handed_a_recheck_that_holds_while_the_handle_does():
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(str(uuid.uuid4()))})
    be = await _served(store)
    assert await be.still_authorized() is True


@pytest.mark.asyncio
async def test_the_recheck_fails_once_the_handle_is_revoked():
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(str(uuid.uuid4()))})
    be = await _served(store)
    del store.mapping[f"{b._KEY_PREFIX}good"]  # revocation is deletion (ADR-0015 dec 2)
    assert await be.still_authorized() is False


@pytest.mark.asyncio
async def test_the_recheck_fails_if_the_handle_is_rebound_to_another_tenant():
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(str(uuid.uuid4()))})
    be = await _served(store)
    store.mapping[f"{b._KEY_PREFIX}good"] = _sql_record(str(uuid.uuid4()))
    assert await be.still_authorized() is False


# --- the capability-use record (ADR-0042) ------------------------------------------------------

class _RelayUntil(FakeBackend):
    """Relays until the re-check fails (or, with ``checks=0``, returns at once: the client closed)."""

    def __init__(self, checks=0, fail_with=None):
        super().__init__()
        self._checks, self._fail_with = checks, fail_with

    async def relay(self, still_authorized):
        await super().relay(still_authorized)
        if self._fail_with is not None:
            raise self._fail_with
        for _ in range(self._checks):
            if not await still_authorized():
                return


def _no_secret_in(store, *secrets):
    flat = [v for e in store.stream for v in e.values()]
    return not any(s in v for s in secrets for v in flat)


@pytest.mark.asyncio
async def test_first_use_is_recorded_once_per_handle_and_sessions_once_per_window_with_a_count():
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    store = FakeAsyncStore({f"{b._KEY_PREFIX}secret-good": _sql_record(cid, hid)})
    for _ in range(3):
        assert await proxy.serve_connection("secret-good", store, lambda binding: _RelayUntil()) is True

    [first] = store.events("first_use")
    assert (first["plane"], first["handle_id"], first["company_id"], first["user"], first["audience"]) == (
        "sql_proxy", hid, cid, "alice", "sql")
    # A kernel can reconnect at will: one opening and one closing per window, the rest counted
    # (ADR-0042 dec 8), carried by the next window's record.
    opened, closed = store.events("sql_session_opened"), store.events("sql_session_closed")
    assert len(opened) == len(closed) == 1
    assert all(e["handle_id"] == hid and e["company_id"] == cid for e in opened + closed)
    assert [e["reason"] for e in closed] == ["client_closed"]
    store.markers.clear()  # the window passes
    assert await proxy.serve_connection("secret-good", store, lambda binding: _RelayUntil()) is True
    assert [e["suppressed"] for e in store.events("sql_session_opened")] == [None, "2"]
    assert all(e["detail"].startswith("duration_seconds=") for e in closed)
    # Statements are not recorded: the setup statements and the kernel's are both absent.
    assert not any("SET ROLE" in v for e in store.stream for v in e.values())
    assert _no_secret_in(store, "secret-good")


@pytest.mark.asyncio
async def test_refusals_are_recorded_and_an_unknown_value_once_never_by_handle():
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    store = FakeAsyncStore({
        f"{b._KEY_PREFIX}secret-api": _sql_record(cid, hid, audience="api"),
        f"{b._KEY_PREFIX}secret-bad": "not-json",
    })
    for handle in ("secret-api", "secret-missing", "secret-missing", "secret-bad"):
        assert await proxy.serve_connection(handle, store, lambda binding: FakeBackend()) is False

    refusals = store.events("refused")
    # An unknown value is recorded once per window by its digest; a real entry's refusal names the
    # entry and carries no digest (ADR-0042 dec 2, 8).
    assert [e["reason"] for e in refusals] == ["wrong_audience", "no_entry", "malformed_entry"]
    assert all(e["plane"] == "sql_proxy" for e in refusals)
    assert (refusals[0]["company_id"], refusals[0]["handle_id"], refusals[0]["audience"]) == (cid, hid, "api")
    assert refusals[0]["digest"] is None
    assert refusals[1]["company_id"] is None and refusals[1]["handle_id"] is None
    assert refusals[1]["digest"] == audit.presented_digest("secret-missing") != refusals[2]["digest"]
    assert store.events("first_use") == [] and store.events("sql_session_opened") == []
    assert _no_secret_in(store, "secret-api", "secret-missing", "secret-bad")


@pytest.mark.asyncio
async def test_a_suspended_tenants_refusal_names_the_tenant(caplog):
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    store = FakeAsyncStore({f"{b._KEY_PREFIX}secret-s": _sql_record(cid, hid),
                            f"nbcap-suspended:{cid}": "1"})
    with caplog.at_level("INFO", logger="notebook_sql_proxy"):
        assert await proxy.serve_connection("secret-s", store, lambda binding: FakeBackend()) is False
    [refused] = store.events("refused")
    assert (refused["reason"], refused["company_id"], refused["handle_id"]) == ("tenant_suspended", cid, hid)
    # The refusal is logged too, without the handle.
    assert "tenant_suspended" in caplog.text and "secret-s" not in caplog.text


@pytest.mark.asyncio
async def test_a_session_ended_by_revocation_records_why():
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(cid, hid)})

    class _RevokedMidSession(_RelayUntil):
        async def relay(self, still_authorized):
            del store.mapping[f"{b._KEY_PREFIX}good"]
            await super().relay(still_authorized)

    assert await proxy.serve_connection("good", store, lambda binding: _RevokedMidSession(checks=3)) is True
    [closed] = store.events("sql_session_closed")
    assert closed["reason"] == "capability_revoked"
    assert "refusal=no_entry" in closed["detail"]


@pytest.mark.asyncio
async def test_a_failed_recheck_and_a_relay_error_are_recorded_as_such():
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(cid, hid)})

    class _StoreDown(_RelayUntil):
        async def relay(self, still_authorized):
            await FakeBackend.relay(self, still_authorized)
            original = store.get

            async def broken(key):
                raise ConnectionError("store unreachable")

            store.get = broken
            try:
                await still_authorized()
            except ConnectionError:
                pass  # the real backend's watchdog treats this as a refusal and ends the session
            finally:
                store.get = original

    assert await proxy.serve_connection("good", store, lambda binding: _StoreDown()) is True
    with pytest.raises(ConnectionError):
        await proxy.serve_connection("good", store, lambda binding: _RelayUntil(fail_with=ConnectionError("x")))
    assert [e["reason"] for e in store.events("sql_session_closed")] == ["recheck_failed", "error"]


@pytest.mark.asyncio
async def test_a_backend_that_never_opened_records_no_session():
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(str(uuid.uuid4()), str(uuid.uuid4()))})

    class _Unreachable(FakeBackend):
        async def connect(self):
            raise ConnectionError("database unreachable")

    with pytest.raises(ConnectionError):
        await proxy.serve_connection("good", store, lambda binding: _Unreachable())
    assert len(store.events("first_use")) == 1
    assert store.events("sql_session_opened") == store.events("sql_session_closed") == []


@pytest.mark.asyncio
async def test_a_handle_minted_before_the_record_has_no_first_use_and_no_session_record():
    # Nothing identifies it to bound its sessions by (ADR-0042 dec 8); it is served all the same.
    store = FakeAsyncStore({f"{b._KEY_PREFIX}old": _sql_record(str(uuid.uuid4()))})
    assert await proxy.serve_connection("old", store, lambda binding: _RelayUntil()) is True
    assert store.events() == []


@pytest.mark.asyncio
async def test_a_record_that_cannot_be_written_refuses_the_connection():
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(str(uuid.uuid4()), str(uuid.uuid4()))})

    async def eval_down(*_args):
        raise ConnectionError("store unreachable")

    store.eval = eval_down
    opened = []
    with pytest.raises(ConnectionError):
        await proxy.serve_connection("good", store, lambda binding: opened.append(binding) or FakeBackend())
    assert opened == []  # no session is served whose first use went unrecorded
