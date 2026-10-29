# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability-use event contract (ADR-0042): what a plane may append, what the writer reads, and
the first-use script run by a real Lua interpreter (fakeredis with lupa)."""
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


def test_an_unknown_value_is_recorded_once_per_window_a_real_entrys_refusal_every_time():
    r = fakeredis.FakeRedis()
    once = r.register_script(audit.UNKNOWN_REFUSAL_SCRIPT)
    for _ in range(3):
        how, keys, args = audit.refusal(plane="api_gateway", handle="random-1", reason="no_entry",
                                        facts={}, source="10.0.0.9")
        assert how == "once"
        once(keys=keys, args=args)
    how, keys, args = audit.refusal(plane="api_gateway", handle="random-2", reason="no_entry", facts={},
                                    source="10.0.0.9")
    once(keys=keys, args=args)
    for _ in range(2):
        how, kwargs = audit.refusal(plane="api_gateway", handle="real", reason="wrong_audience",
                                    facts={"handle_id": HID, "company_id": CID, "audience": "sql"},
                                    source="10.0.0.9")
        assert how == "append"
        r.xadd(**kwargs)
    events = [audit.decode(f) for _, f in r.xrange(audit.STREAM_KEY)]
    unknown = [e for e in events if e["digest"]]
    assert len(unknown) == 2 and {e["digest"] for e in unknown} == {
        audit.presented_digest("random-1"), audit.presented_digest("random-2")}
    named = [e for e in events if e["handle_id"]]
    assert len(named) == 2 and all(e["digest"] is None for e in named)
    assert 0 < r.ttl(audit.unknown_refusal_key(audit.presented_digest("random-1"), "api_gateway")) <= 3600


def test_a_flood_of_distinct_values_from_one_source_is_recorded_up_to_its_limit():
    r = fakeredis.FakeRedis()
    once = r.register_script(audit.UNKNOWN_REFUSAL_SCRIPT)
    results = []
    for i in range(audit.SOURCE_LIMIT + 5):
        _how, keys, args = audit.refusal(plane="sql_proxy", handle=f"random-{i}", reason="no_entry",
                                         facts={}, source="10.0.0.9")
        results.append(once(keys=keys, args=args))
    assert results.count(1) == audit.SOURCE_LIMIT and results.count(-1) == 1 and results.count(-2) == 4
    # Another source, and another plane, have limits of their own.
    _how, keys, args = audit.refusal(plane="sql_proxy", handle="random-x", reason="no_entry", facts={},
                                     source="10.0.0.10")
    assert once(keys=keys, args=args) == 1
    assert r.xlen(audit.STREAM_KEY) == audit.SOURCE_LIMIT + 1
    assert audit.decode(r.xrange(audit.STREAM_KEY)[0][1])["detail"] == "source=10.0.0.9"
