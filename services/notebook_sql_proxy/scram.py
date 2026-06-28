# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
SCRAM-SHA-256 client (RFC 5802 / RFC 7677), the auth method the proxy uses to open its
**upstream** privileged connection to TimescaleDB (ADR-0017 mandates ``scram-sha-256``).

This is the pure cryptographic core: build the client-first and client-final messages from the
server's challenge and verify the server signature. It performs no I/O — ``server.py`` exchanges
the messages over the SASL wire. Being pure, it is validated here against the published RFC 7677
test vector, so the crypto is correct even though the end-to-end exchange against a live Postgres
is part of the cluster-bound integration work (issue #19).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Dict

_DIGEST = "sha256"
GS2_HEADER = b"n,,"  # no channel binding, no authzid


def _h(data: bytes) -> bytes:
    return hashlib.new(_DIGEST, data).digest()


def _hmac(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, _DIGEST).digest()


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _parse_server_message(message: str) -> Dict[str, str]:
    """Parse a SCRAM ``a=...,b=...`` attribute message into a dict."""
    out: Dict[str, str] = {}
    for item in message.split(","):
        if "=" in item:
            key, _, value = item.partition("=")
            out[key] = value
    return out


def salted_password(password: str, salt: bytes, iterations: int) -> bytes:
    """PBKDF2(HMAC-SHA-256) of the password — the ``SaltedPassword`` of RFC 5802."""
    return hashlib.pbkdf2_hmac(_DIGEST, password.encode("utf-8"), salt, iterations)


@dataclass
class ScramClient:
    """A one-shot SCRAM-SHA-256 client exchange.

    Usage: ``client_first()`` → send to server → ``client_final(server_first)`` → send → then
    ``verify_server_final(server_final)``. ``client_nonce`` is injected (not generated here) so
    the exchange is deterministic and testable; ``server.py`` supplies a random nonce in
    production.
    """

    username: str
    password: str
    client_nonce: str

    _auth_message: str = ""

    def client_first(self) -> bytes:
        """The SASLInitialResponse body: ``n,,n=<user>,r=<nonce>``."""
        self._client_first_bare = f"n={_saslprep(self.username)},r={self.client_nonce}"
        return GS2_HEADER + self._client_first_bare.encode("utf-8")

    def client_final(self, server_first: bytes) -> bytes:
        """Consume the server-first message and produce the client-final message bytes."""
        server_first_str = server_first.decode("utf-8")
        attrs = _parse_server_message(server_first_str)
        server_nonce = attrs["r"]
        if not server_nonce.startswith(self.client_nonce):
            raise ValueError("server nonce does not extend the client nonce")
        salt = base64.b64decode(attrs["s"])
        iterations = int(attrs["i"])

        salted = salted_password(self.password, salt, iterations)
        client_key = _hmac(salted, b"Client Key")
        stored_key = _h(client_key)

        channel_binding = base64.b64encode(GS2_HEADER).decode("ascii")
        client_final_without_proof = f"c={channel_binding},r={server_nonce}"
        self._auth_message = (
            f"{self._client_first_bare},{server_first_str},{client_final_without_proof}"
        )
        client_signature = _hmac(stored_key, self._auth_message.encode("utf-8"))
        client_proof = _xor(client_key, client_signature)

        # Stash the server key for the server-final verification step.
        self._server_key = _hmac(salted, b"Server Key")

        proof_b64 = base64.b64encode(client_proof).decode("ascii")
        return f"{client_final_without_proof},p={proof_b64}".encode("utf-8")

    def verify_server_final(self, server_final: bytes) -> bool:
        """Verify the server's signature in the server-final message (mutual authentication)."""
        attrs = _parse_server_message(server_final.decode("utf-8"))
        if "v" not in attrs:
            return False
        expected = _hmac(self._server_key, self._auth_message.encode("utf-8"))
        return hmac.compare_digest(base64.b64decode(attrs["v"]), expected)


def _saslprep(value: str) -> str:
    """Minimal SASLprep: escape the two characters SCRAM reserves in the ``n=`` attribute.

    A full SASLprep (RFC 4013) normalization is unnecessary for our principals (ASCII role
    names); only ``=`` and ``,`` must be escaped per RFC 5802.
    """
    return value.replace("=", "=3D").replace(",", "=2C")
