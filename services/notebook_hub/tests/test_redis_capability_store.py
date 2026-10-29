# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Redis adapter against a real Redis protocol implementation (fakeredis, with Lua).

The other hub tests drive the capability logic through a dict fake, which cannot show that a mint and
its record are one MULTI or that a revocation runs the contract's script (ADR-0042 dec 3). This
does: the adapter's own commands, on a server that executes them.
"""
import json
import os
import sys
import uuid

import fakeredis
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import capabilities as cap  # noqa: E402
from industryflow_contracts import capability_audit as audit  # noqa: E402

CID = str(uuid.uuid4())


@pytest.fixture
def client():
    return fakeredis.FakeRedis()


@pytest.fixture
def transactions(client):
    """Each pipeline the adapter executes: (was it MULTI/EXEC, the commands it carried)."""
    seen = []
    real = client.pipeline

    def pipeline(transaction=True, shard_hint=None):
        pipe = real(transaction=transaction, shard_hint=shard_hint)
        execute = pipe.execute

        def recorded(*a, **k):
            seen.append((transaction, [str(c[0][0]).upper() for c in pipe.command_stack]))
            return execute(*a, **k)

        pipe.execute = recorded
        return pipe

    client.pipeline = pipeline
    return seen


def _events(client):
    return [audit.decode(fields) for _id, fields in client.xrange(audit.STREAM_KEY)]


def test_a_mint_its_index_and_its_record_are_one_transaction(client, transactions):
    store = cap.RedisCapabilityStore(client)
    handle = cap.mint(store, user="alice", company_id=CID, audience=cap.AUDIENCE_API,
                      ttl_seconds=900, server_name="")
    assert transactions == [(True, ["HSET", "EXPIRE", "SET", "XADD"])]
    entry = json.loads(client.get(f"nbcap:{handle}"))
    (minted,) = _events(client)
    assert minted["kind"] == "minted" and minted["handle_id"] == entry["handle_id"]
    assert 0 < client.ttl(f"nbcap:{handle}") <= 900
    assert client.hgetall("nbcap-user:alice") == {handle.encode(): b""}
    assert handle.encode() not in b"".join(v for _i, f in client.xrange(audit.STREAM_KEY) for v in f.values())


def test_a_revocation_runs_the_contract_script_and_records_only_a_live_entry(client):
    store = cap.RedisCapabilityStore(client)
    live = cap.mint(store, user="alice", company_id=CID, audience=cap.AUDIENCE_SQL,
                    ttl_seconds=900, server_name="")
    gone = cap.mint(store, user="alice", company_id=CID, audience=cap.AUDIENCE_API,
                    ttl_seconds=900, server_name="")
    client.delete(f"nbcap:{gone}")  # expired on its own

    assert cap.revoke_server(store, "alice", "", reason=cap.REASON_SERVER_STOPPED) == 2
    revoked = [e for e in _events(client) if e["kind"] == "revoked"]
    assert [(e["audience"], e["reason"], e["company_id"]) for e in revoked] == [
        ("sql", "server_stopped", CID)]
    assert client.get(f"nbcap:{live}") is None
    assert client.hgetall("nbcap-user:alice") == {}


def test_environment_events_are_bounded_appends(client):
    store = cap.RedisCapabilityStore(client)
    cap.record_environment_started(store, user="alice", company_id=CID, server_name="")
    cap.record_environment_stopped(store, user="alice", company_id=CID, server_name="",
                                   reason=cap.REASON_SERVER_STOPPED)
    assert [e["kind"] for e in _events(client)] == ["environment_started", "environment_stopped"]
