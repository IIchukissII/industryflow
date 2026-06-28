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


def test_server_nonce_must_extend_client_nonce():
    client = _client()
    client.client_first()
    with pytest.raises(ValueError):
        client.client_final(b"r=differentnonce,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096")
