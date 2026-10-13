# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""SCRAM-SHA-256 client validated against the RFC 7677 §3 test vector (ADR-0017)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import scram  # noqa: E402

# RFC 7677 section 3 worked example.
_USER = "user"
_PASSWORD = "pencil"
_CLIENT_NONCE = "rOprNGfwEbeRWgbNEkqO"
_SERVER_FIRST = (
    b"r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
    b"s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096"
)
_CLIENT_FINAL = (
    b"c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
    b"p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
)
_SERVER_FINAL = b"v=6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def _client():
    return scram.ScramClient(_USER, _PASSWORD, _CLIENT_NONCE)


def test_client_first_matches_rfc_vector():
    assert _client().client_first() == b"n,,n=user,r=" + _CLIENT_NONCE.encode()


def test_client_final_matches_rfc_vector():
    client = _client()
    client.client_first()
    assert client.client_final(_SERVER_FIRST) == _CLIENT_FINAL


def test_server_signature_verifies():
    client = _client()
    client.client_first()
    client.client_final(_SERVER_FIRST)
    assert client.verify_server_final(_SERVER_FINAL) is True


def test_tampered_server_signature_is_rejected():
    client = _client()
    client.client_first()
    client.client_final(_SERVER_FIRST)
    assert client.verify_server_final(b"v=AAAATRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=") is False


def test_verifier_matches_the_rfc_vector_keys():
    """The stored verifier Postgres would compute for the RFC 7677 password and salt."""
    import base64

    v = scram.scram_verifier(_PASSWORD, salt=base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ=="), iterations=4096)
    assert v.startswith("SCRAM-SHA-256$4096:W22ZaJ0SNY7soEsUEjb6gQ==$")
    # The server signature the RFC vector expects can be recomputed from the verifier's ServerKey,
    # so a server holding only this verifier authenticates the RFC exchange.
    import hashlib
    import hmac

    server_key = base64.b64decode(v.rsplit(":", 1)[1])
    client = _client()
    client.client_first()
    client.client_final(_SERVER_FIRST)
    signature = hmac.new(server_key, client._auth_message.encode(), hashlib.sha256).digest()
    assert b"v=" + base64.b64encode(signature) == _SERVER_FINAL


def test_verifier_accepts_the_clients_proof():
    """A server holding only the verifier accepts the proof a client derives from the password."""
    import base64
    import hashlib
    import hmac

    v = scram.scram_verifier("derived-password")
    _, rest = v.split("$", 1)
    iter_salt, keys = rest.split("$")
    iterations, salt = iter_salt.split(":")
    stored_key = base64.b64decode(keys.split(":")[0])

    client = scram.ScramClient("u", "derived-password", "clientnonce")
    client.client_first()
    final = client.client_final(f"r=clientnonceSERVER,s={salt},i={iterations}".encode())
    proof = base64.b64decode(final.decode().rsplit("p=", 1)[1])
    signature = hmac.new(stored_key, client._auth_message.encode(), hashlib.sha256).digest()
    client_key = bytes(x ^ y for x, y in zip(proof, signature))
    assert hashlib.sha256(client_key).digest() == stored_key


def test_verifier_salts_are_fresh():
    assert scram.scram_verifier("p") != scram.scram_verifier("p")


def test_server_nonce_must_extend_client_nonce():
    client = _client()
    client.client_first()
    with pytest.raises(ValueError):
        client.client_final(b"r=differentnonce,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096")
