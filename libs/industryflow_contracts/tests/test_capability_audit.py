# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability-use event contract (ADR-0042): what a plane may append, what the writer reads, and
the first-use script run by a real Lua interpreter (fakeredis with lupa)."""
import asyncio
import uuid

import pytest

from industryflow_contracts import capability_audit as audit

fakeredis = pytest.importorskip("fakeredis")

CID = "00000000-0000-4000-8000-0000000000c1"
HID = str(uuid.uuid4())


def _first_use(**over):
    kw = dict(plane="sql_proxy", user="alice", company_id=CID, audience="sql", handle_id=HID)
    kw.update(over)
    return audit.event(audit.FIRST_USE, **kw)


def test_an_event_round_trips_through_the_writer():
    fields = audit.event(audit.MINTED, plane="hub", user="alice", company_id=CID.upper(),
                         audience="sql", handle_id=HID, detail="server=lab")
    out = audit.decode({k.encode(): v.encode() for k, v in fields.items()})
    assert out["company_id"] == CID and out["handle_id"] == HID and out["kind"] == "minted"
    assert out["digest"] is None and out["reason"] is None


@pytest.mark.parametrize("kw", [
    {"kind": "used"}, {"plane": "kernel"}, {"audience": "admin"}, {"company_id": "../x"},
    {"handle_id": "a-bearer-handle-is-not-an-id"},
])
def test_a_malformed_event_is_refused_before_it_is_appended(kw):
    args = dict(plane="hub", user="alice", company_id=CID, audience="api", handle_id=HID)
    kind = kw.pop("kind", audit.MINTED)
    args.update(kw)
    with pytest.raises(ValueError):
        audit.event(kind, **args)


def test_the_writer_refuses_what_no_plane_produces():
    with pytest.raises(ValueError):
        audit.decode({b"kind": b"minted", b"plane": b"kernel", b"at": b"2026-10-29T00:00:00+00:00"})
    with pytest.raises(ValueError):
        audit.decode({b"kind": b"minted", b"plane": b"hub", b"at": b"2026-10-29T00:00:00"})


def test_a_presented_digest_is_short_and_stable():
    handle = "x" * 43
    assert audit.presented_digest(handle) == audit.presented_digest(handle.encode())
    assert len(audit.presented_digest(handle)) == 16 and handle not in audit.presented_digest(handle)


def test_first_use_is_recorded_once_per_handle_and_plane():
    r = fakeredis.FakeRedis()
    script = r.register_script(audit.FIRST_USE_SCRIPT)
    for fields in (_first_use(), _first_use(), _first_use(plane="api_gateway", audience="api")):
        keys, args = audit.first_use_script_args(fields)
        script(keys=keys, args=args)
    entries = r.xrange(audit.STREAM_KEY)
    assert [audit.decode(f)["plane"] for _, f in entries] == ["sql_proxy", "api_gateway"]
    assert 0 < r.ttl(audit.first_use_key(HID, "sql_proxy")) <= audit.FIRST_USE_MARKER_TTL_SECONDS


def test_first_use_script_args_need_a_first_use_with_a_handle():
    with pytest.raises(ValueError):
        audit.first_use_script_args(audit.event(audit.REFUSED, plane="sql_proxy", digest="ab"))


def test_a_removal_is_recorded_only_when_it_removed_the_entry():
    r = fakeredis.FakeRedis()
    script = r.register_script(audit.DELETE_RECORDED_SCRIPT)
    r.set("nbcap:h", "{}")
    fields = audit.event(audit.REVOKED, plane="hub", handle_id=HID, reason="server_stopped")
    keys, args = audit.delete_recorded_args("nbcap:h", fields)
    assert script(keys=keys, args=args) == 1
    assert script(keys=keys, args=args) == 0
    assert r.get("nbcap:h") is None and len(r.xrange(audit.STREAM_KEY)) == 1
    with pytest.raises(ValueError):
        audit.delete_recorded_args("nbcap:h", audit.event(audit.MINTED, plane="hub"))


def test_a_plain_append_is_bounded():
    r = fakeredis.FakeRedis()
    r.xadd(**audit.append_args(audit.event(audit.REFUSED, plane="cold_broker", digest="0" * 16,
                                           reason="no_entry")))
    assert audit.decode(r.xrange(audit.STREAM_KEY)[0][1])["reason"] == "no_entry"


def test_a_nul_in_a_name_is_replaced_not_refused():
    fields = audit.event(audit.REFUSED, plane="sql_proxy", user="ali\x00ce", digest="0" * 16)
    assert "\x00" not in fields["user"]
    assert "\x00" not in audit.decode({**fields, "detail": "a\x00b"})["detail"]


@pytest.mark.parametrize("digest", ["ab", "0" * 64, "G" * 16])
def test_only_a_presented_digest_is_a_digest(digest):
    with pytest.raises(ValueError):
        audit.event(audit.REFUSED, plane="cold_broker", digest=digest)
    with pytest.raises(ValueError):
        audit.decode({"kind": "refused", "plane": "cold_broker", "at": "2026-10-29T00:00:00+00:00",
                      "digest": digest})


def _eval(r, script, keys, args):
    return r.register_script(script)(keys=keys, args=args)


def test_an_unknown_value_is_recorded_once_per_window_by_digest():
    r = fakeredis.FakeRedis()
    for handle in ("random-1", "random-1", "random-1", "random-2"):
        script, keys, args = audit.refusal(plane="api_gateway", handle=handle, reason="no_entry",
                                           facts={}, source="10.0.0.9")
        assert script == audit.UNKNOWN_REFUSAL_SCRIPT
        _eval(r, script, keys, args)
    events = [audit.decode(f) for _, f in r.xrange(audit.STREAM_KEY)]
    assert {e["digest"] for e in events} == {audit.presented_digest("random-1"), audit.presented_digest("random-2")}
    assert len(events) == 2
    assert 0 < r.ttl(audit.unknown_refusal_key(audit.presented_digest("random-1"), "api_gateway")) <= 3600


REAL = {"handle_id": HID, "company_id": CID, "audience": "sql", "user": "alice"}


def _refuse_real(r, reason="wrong_audience", plane="api_gateway"):
    script, keys, args = audit.refusal(plane=plane, handle="real", reason=reason, facts=REAL, source="10.0.0.9")
    assert script == audit.REPEAT_SCRIPT
    return _eval(r, script, keys, args)


def test_a_real_entrys_refusals_are_recorded_once_per_window_with_the_count_of_the_rest():
    r = fakeredis.FakeRedis()
    # A kernel sending its own handle to the wrong plane, many times: one row, not one per request.
    assert [_refuse_real(r) for _ in range(500)] == [1] + [0] * 499
    # Another reason and another plane are windows of their own.
    assert _refuse_real(r, reason="tenant_inactive") == 1
    assert _refuse_real(r, plane="cold_broker") == 1
    first = [audit.decode(f) for _, f in r.xrange(audit.STREAM_KEY)]
    assert len(first) == 3 and all(e["digest"] is None and e["handle_id"] == HID for e in first)
    assert all(e["suppressed"] is None for e in first)
    window = audit.repeat_keys(audit.event(audit.REFUSED, plane="api_gateway", reason="wrong_audience",
                                           handle_id=HID))
    assert 0 < r.ttl(window[0]) <= audit.REPEAT_WINDOW_SECONDS
    assert audit.REPEAT_WINDOW_SECONDS < r.ttl(window[1]) <= audit.REPEAT_COUNT_TTL_SECONDS
    # The window passes: the next refusal is recorded, carrying how many were not.
    r.delete(window[0])
    assert _refuse_real(r) == 1
    last = audit.decode(r.xrange(audit.STREAM_KEY)[-1][1])
    assert (last["reason"], last["suppressed"]) == ("wrong_audience", "499")
    assert r.get(window[1]) is None


def test_a_sql_sessions_events_are_bounded_per_window_like_a_refusal():
    r = fakeredis.FakeRedis()
    for _ in range(50):
        for kind in (audit.SQL_SESSION_OPENED, audit.SQL_SESSION_CLOSED):
            fields = audit.event(kind, plane="sql_proxy", user="alice", company_id=CID, audience="sql",
                                 handle_id=HID)
            _eval(r, audit.REPEAT_SCRIPT, *audit.repeat_script_args(fields))
    assert [audit.decode(f)["kind"] for _, f in r.xrange(audit.STREAM_KEY)] == [
        audit.SQL_SESSION_OPENED, audit.SQL_SESSION_CLOSED]


def test_only_a_real_entrys_repeatable_events_go_through_the_window():
    with pytest.raises(ValueError):
        audit.repeat_script_args(audit.event(audit.MINTED, plane="hub", handle_id=HID))
    with pytest.raises(ValueError):
        audit.repeat_script_args(audit.event(audit.REFUSED, plane="hub"))


@pytest.mark.parametrize("count", ["0", "-1", "x", "99999999999", "1.5"])
def test_a_suppressed_count_is_a_positive_integer(count):
    with pytest.raises(ValueError):
        audit.decode({"kind": "refused", "plane": "hub", "at": "2026-10-29T00:00:00+00:00", "suppressed": count})


@pytest.mark.parametrize("peer, reported, source", [
    ("10.0.0.2", "203.0.113.7", "203.0.113.7"),         # relayed by the edge: the client it reports
    ("10.0.0.2", "203.0.113.7, 10.0.0.9", "203.0.113.7"),
    ("10.0.0.2", None, "10.0.0.2"),
    ("10.1.4.4", "203.0.113.7", "203.0.113.7"),         # an edge named by its network
    ("127.0.0.1", "203.0.113.7", "203.0.113.7"),        # an edge named by its host name
    ("172.20.0.5", "203.0.113.7", "172.20.0.5"),        # a kernel naming a source of its choice
    ("not-an-address", "203.0.113.7", "not-an-address"),
    (None, "203.0.113.7", None),
])
def test_a_reported_client_address_is_believed_from_the_edge_alone(peer, reported, source):
    edge = audit.EdgePeers("10.0.0.2, 10.1.4.0/24, localhost, edge.invalid")
    assert asyncio.run(audit.client_source(peer, reported, edge)) == source


def test_no_edge_configured_believes_no_reported_address():
    assert asyncio.run(audit.client_source("10.0.0.2", "203.0.113.7", audit.EdgePeers(""))) == "10.0.0.2"


def test_a_flood_of_distinct_values_from_one_source_is_recorded_up_to_its_limit():
    r = fakeredis.FakeRedis()
    once = r.register_script(audit.UNKNOWN_REFUSAL_SCRIPT)
    results = []
    for i in range(audit.SOURCE_LIMIT + 5):
        _script, keys, args = audit.refusal(plane="sql_proxy", handle=f"random-{i}", reason="no_entry",
                                         facts={}, source="10.0.0.9")
        results.append(once(keys=keys, args=args))
    assert results.count(1) == audit.SOURCE_LIMIT and results.count(-1) == 1 and results.count(-2) == 4
    # Another source, and another plane, have limits of their own.
    _script, keys, args = audit.refusal(plane="sql_proxy", handle="random-x", reason="no_entry", facts={},
                                     source="10.0.0.10")
    assert once(keys=keys, args=args) == 1
    assert r.xlen(audit.STREAM_KEY) == audit.SOURCE_LIMIT + 1
    assert audit.decode(r.xrange(audit.STREAM_KEY)[0][1])["detail"] == "source=10.0.0.9"
