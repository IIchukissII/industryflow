# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the pure Postgres wire codec (ADR-0015)."""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import wire  # noqa: E402


def test_startup_message_roundtrip():
    params = {"user": "sqlproxy", "database": "industryflow", "application_name": "kernel"}
    packet = wire.build_startup_message(params)
    # The packet carries its own Int32 length prefix; parse_startup_packet wants the body after it.
    (length,) = struct.unpack("!I", packet[:4])
    assert length == len(packet)
    code, parsed = wire.parse_startup_packet(packet[4:])
    assert code == wire.PROTOCOL_VERSION_3
    assert parsed == params


def test_ssl_request_is_recognized():
    body = struct.pack("!I", wire.SSL_REQUEST_CODE)
    code, params = wire.parse_startup_packet(body)
    assert code == wire.SSL_REQUEST_CODE
    assert params == {}


def test_startup_rejects_unterminated_params():
    body = struct.pack("!I", wire.PROTOCOL_VERSION_3) + b"user\x00alice"  # missing final NULs
    with pytest.raises(wire.ProtocolError):
        wire.parse_startup_packet(body)


def test_password_message_roundtrip():
    msg = wire.build_password_message("cap-handle-xyz")
    type_byte, payload, rest = wire.split_message(msg)
    assert type_byte == wire.MSG_PASSWORD
    assert rest == b""
    assert wire.parse_password_message(payload) == "cap-handle-xyz"


def test_authentication_cleartext_and_ok():
    type_byte, payload, _ = wire.split_message(wire.build_authentication_cleartext_password())
    assert type_byte == wire.MSG_AUTHENTICATION
    sub, rest = wire.parse_authentication(payload)
    assert sub == wire.AUTH_CLEARTEXT_PASSWORD and rest == b""

    _, ok_payload, _ = wire.split_message(wire.build_authentication_ok())
    assert wire.parse_authentication(ok_payload)[0] == wire.AUTH_OK


def test_error_response_roundtrips_fields():
    msg = wire.build_error_response("capability handle refused")
    type_byte, payload, _ = wire.split_message(msg)
    assert type_byte == wire.MSG_ERROR_RESPONSE
    fields = wire.parse_error_response(payload)
    assert fields["S"] == "FATAL"
    assert fields["C"] == "28000"  # invalid_authorization_specification
    assert fields["M"] == "capability handle refused"


def test_split_message_signals_incomplete():
    full = wire.build_query("SELECT 1")
    with pytest.raises(wire.ProtocolError):
        wire.split_message(full[:3])  # only part of the header


def test_split_message_leaves_trailing_bytes():
    two = wire.build_query("SELECT 1") + wire.build_ready_for_query()
    type_byte, _payload, rest = wire.split_message(two)
    assert type_byte == wire.MSG_QUERY
    # The second message is returned intact for the next split.
    t2, _, rest2 = wire.split_message(rest)
    assert t2 == wire.MSG_READY_FOR_QUERY and rest2 == b""
