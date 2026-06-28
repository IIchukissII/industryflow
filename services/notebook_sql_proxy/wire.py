# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PostgreSQL v3 wire-protocol codec (ADR-0015 dec 5-6).

The SQL proxy speaks the Postgres frontend/backend protocol to *two* peers: it is a server to
the notebook kernel (which connects with a normal Postgres driver) and a client to the real
database. This module is the **pure** message codec for that protocol — parse and build the
handful of messages the proxy's handshake needs — with no I/O. The socket handling, the upstream
connection, and the byte relay live in ``server.py``; this layer is fully unit-testable on bytes.

Only the messages the proxy actually constructs or inspects are implemented; the relay forwards
everything else verbatim, so a full message zoo is unnecessary. Protocol reference: the Postgres
"Message Formats" and "Protocol Flow" chapters.

Framing
-------
A *regular* message is ``Int8 type | Int32 length | payload`` where ``length`` counts itself but
not the type byte. The very first message a client sends (StartupMessage / SSLRequest /
GSSENCRequest) is special: it has **no type byte**, just ``Int32 length | Int32 code | …``.
"""
from __future__ import annotations

import struct
from typing import Dict, Optional, Tuple

PROTOCOL_VERSION_3 = 196608  # 0x00030000 — protocol 3.0
SSL_REQUEST_CODE = 80877103  # 1234 << 16 | 5679
GSSENC_REQUEST_CODE = 80877104  # 1234 << 16 | 5680
CANCEL_REQUEST_CODE = 80877102  # 1234 << 16 | 5678

# Backend → frontend message type bytes.
MSG_AUTHENTICATION = b"R"
MSG_ERROR_RESPONSE = b"E"
MSG_READY_FOR_QUERY = b"Z"
MSG_PARAMETER_STATUS = b"S"
MSG_BACKEND_KEY_DATA = b"K"
# Frontend → backend message type bytes.
MSG_PASSWORD = b"p"
MSG_QUERY = b"Q"
MSG_TERMINATE = b"X"

# Authentication request sub-codes (the Int32 after the message header).
AUTH_OK = 0
AUTH_CLEARTEXT_PASSWORD = 3
AUTH_MD5_PASSWORD = 5
AUTH_SASL = 10

# ReadyForQuery transaction-status indicators.
TXN_IDLE = b"I"


class ProtocolError(ValueError):
    """The bytes on the wire are not a well-formed message we can parse."""


# --------------------------------------------------------------------------- startup (untyped)

def parse_startup_packet(data: bytes) -> Tuple[int, Dict[str, str]]:
    """Parse a startup-phase packet body (the bytes *after* the Int32 length prefix).

    Returns ``(code, params)``. For a real StartupMessage ``code`` is the protocol version
    (``PROTOCOL_VERSION_3``) and ``params`` is the parameter map (``user``, ``database``, …).
    For an SSLRequest/GSSENCRequest/CancelRequest the ``code`` is the corresponding sentinel and
    ``params`` is empty. ``data`` must NOT include the 4-byte length prefix.
    """
    if len(data) < 4:
        raise ProtocolError("startup packet too short for a version/code field")
    (code,) = struct.unpack("!I", data[:4])
    if code in (SSL_REQUEST_CODE, GSSENC_REQUEST_CODE, CANCEL_REQUEST_CODE):
        return code, {}
    if code != PROTOCOL_VERSION_3:
        raise ProtocolError(f"unsupported protocol/startup code: {code}")

    body = data[4:]
    params: Dict[str, str] = {}
    # The body is a sequence of NUL-terminated key/value C strings, terminated by a zero-length
    # string (a lone NUL). Walk pairs until that terminator; the final NUL leaves a trailing
    # empty token after split, which the empty-key break stops on.
    if not body.endswith(b"\x00"):
        raise ProtocolError("startup parameters not NUL-terminated")
    tokens = body.split(b"\x00")
    idx = 0
    while idx < len(tokens):
        key = tokens[idx]
        if key == b"":
            break  # the parameter-area terminator
        if idx + 1 >= len(tokens):
            raise ProtocolError("startup parameter key without a value")
        params[key.decode("utf-8")] = tokens[idx + 1].decode("utf-8")
        idx += 2
    else:
        raise ProtocolError("startup parameters missing terminator")
    return code, params


def build_ssl_request() -> bytes:
    """The SSLRequest packet: ``Int32 len=8 | Int32 code``. Sent before the StartupMessage to ask
    the server to upgrade the connection to TLS (the proxy → TimescaleDB hop, ADR-0017)."""
    return struct.pack("!II", 8, SSL_REQUEST_CODE)


def build_startup_message(params: Dict[str, str]) -> bytes:
    """Build a StartupMessage (used by the proxy to open the *upstream* connection)."""
    body = struct.pack("!I", PROTOCOL_VERSION_3)
    for key, value in params.items():
        body += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"
    body += b"\x00"  # final terminator
    return struct.pack("!I", len(body) + 4) + body


# --------------------------------------------------------------------------- regular framing

def build_message(type_byte: bytes, payload: bytes = b"") -> bytes:
    """Frame a regular (typed) message: ``type | Int32 len | payload``."""
    if len(type_byte) != 1:
        raise ValueError("type_byte must be exactly one byte")
    return type_byte + struct.pack("!I", len(payload) + 4) + payload


def split_message(data: bytes) -> Tuple[bytes, bytes, bytes]:
    """Split one regular message off the front of ``data``.

    Returns ``(type_byte, payload, rest)``. Raises ProtocolError if a complete message is not yet
    present, so callers buffering a stream should catch it and read more.
    """
    if len(data) < 5:
        raise ProtocolError("incomplete message header")
    type_byte = data[0:1]
    (length,) = struct.unpack("!I", data[1:5])
    total = 1 + length
    if len(data) < total:
        raise ProtocolError("incomplete message body")
    return type_byte, data[5:total], data[total:]


# --------------------------------------------------------------------------- auth messages

def build_authentication_request(code: int, payload: bytes = b"") -> bytes:
    """Build an ``Authentication*`` message (type ``R``) with the given sub-code."""
    return build_message(MSG_AUTHENTICATION, struct.pack("!I", code) + payload)


def build_authentication_cleartext_password() -> bytes:
    """Tell the kernel to send its password (which, for us, is the capability handle)."""
    return build_authentication_request(AUTH_CLEARTEXT_PASSWORD)


def build_authentication_ok() -> bytes:
    return build_authentication_request(AUTH_OK)


def parse_authentication(payload: bytes) -> Tuple[int, bytes]:
    """Parse an Authentication message payload into ``(sub_code, rest)``."""
    if len(payload) < 4:
        raise ProtocolError("authentication message too short")
    (code,) = struct.unpack("!I", payload[:4])
    return code, payload[4:]


def parse_password_message(payload: bytes) -> str:
    """Extract the cleartext password from a PasswordMessage payload (NUL-terminated C string)."""
    if not payload.endswith(b"\x00"):
        raise ProtocolError("password message not NUL-terminated")
    return payload[:-1].decode("utf-8")


def build_password_message(password: str) -> bytes:
    return build_message(MSG_PASSWORD, password.encode("utf-8") + b"\x00")


# --------------------------------------------------------------------------- query / control

def build_query(sql: str) -> bytes:
    """Build a simple-protocol Query message (used to run the SET ROLE setup statements)."""
    return build_message(MSG_QUERY, sql.encode("utf-8") + b"\x00")


def build_ready_for_query(status: bytes = TXN_IDLE) -> bytes:
    if len(status) != 1:
        raise ValueError("ReadyForQuery status must be one byte")
    return build_message(MSG_READY_FOR_QUERY, status)


def build_error_response(message: str, *, code: str = "28000", severity: str = "FATAL") -> bytes:
    """Build an ErrorResponse. Default SQLSTATE 28000 = invalid_authorization_specification —
    the right class for a refused capability handle (ADR-0015 dec 1-2)."""
    # Fields are tagged C strings, terminated by a zero byte. S=severity, V=non-localized
    # severity, C=SQLSTATE, M=message.
    body = (
        b"S" + severity.encode() + b"\x00"
        + b"V" + severity.encode() + b"\x00"
        + b"C" + code.encode() + b"\x00"
        + b"M" + message.encode("utf-8") + b"\x00"
        + b"\x00"
    )
    return build_message(MSG_ERROR_RESPONSE, body)


def parse_error_response(payload: bytes) -> Dict[str, str]:
    """Parse an ErrorResponse payload into its tagged fields (used when relaying upstream errors)."""
    fields: Dict[str, str] = {}
    for chunk in payload.split(b"\x00"):
        if not chunk:
            continue
        fields[chunk[0:1].decode("latin-1")] = chunk[1:].decode("utf-8", "replace")
    return fields
