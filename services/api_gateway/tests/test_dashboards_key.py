# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The dashboards' sign-on key one-shot (ADR-0016 dec 7): the two halves always match."""
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import dashboards_key as dk


def _pair(tmp_path):
    signing, verify = tmp_path / "signing", tmp_path / "verify"
    signing.mkdir()
    verify.mkdir()
    return signing, verify


def _public_of(private_path):
    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    return key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)


def test_first_run_generates_a_matching_pair_with_tight_modes(tmp_path):
    signing, verify = _pair(tmp_path)
    assert dk.ensure(signing, verify, None) == "generated"
    private, public = signing / dk.PRIVATE_NAME, verify / dk.PUBLIC_NAME
    assert public.read_bytes() == _public_of(private)
    assert oct(private.stat().st_mode & 0o777) == "0o400"
    assert oct(public.stat().st_mode & 0o777) == "0o644"
    assert not list(signing.glob("*.new")) and not list(verify.glob("*.new"))


def test_a_second_run_keeps_the_key(tmp_path):
    signing, verify = _pair(tmp_path)
    dk.ensure(signing, verify, None)
    before = (signing / dk.PRIVATE_NAME).read_bytes()
    assert dk.ensure(signing, verify, None) == "kept"
    assert (signing / dk.PRIVATE_NAME).read_bytes() == before


@pytest.mark.parametrize("stale", ["missing", "other key"])
def test_a_public_half_that_does_not_match_is_derived_again(tmp_path, stale):
    signing, verify = _pair(tmp_path)
    dk.ensure(signing, verify, None)
    public = verify / dk.PUBLIC_NAME
    if stale == "missing":
        public.unlink()
    else:
        other = ec.generate_private_key(ec.SECP256R1()).public_key()
        os.chmod(public, 0o644)
        public.write_bytes(other.public_bytes(serialization.Encoding.PEM,
                                              serialization.PublicFormat.SubjectPublicKeyInfo))
    assert dk.ensure(signing, verify, None) == "public half re-derived"
    assert public.read_bytes() == _public_of(signing / dk.PRIVATE_NAME)


def test_a_removed_private_half_with_an_old_public_half_yields_a_new_matching_pair(tmp_path):
    # The case a check for "both files exist" got wrong: only the signing volume was removed.
    signing, verify = _pair(tmp_path)
    dk.ensure(signing, verify, None)
    old_public = (verify / dk.PUBLIC_NAME).read_bytes()
    (signing / dk.PRIVATE_NAME).unlink()
    assert dk.ensure(signing, verify, None) == "generated"
    assert (verify / dk.PUBLIC_NAME).read_bytes() != old_public
    assert (verify / dk.PUBLIC_NAME).read_bytes() == _public_of(signing / dk.PRIVATE_NAME)


def test_a_key_of_another_kind_is_refused_not_replaced(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa

    signing, verify = _pair(tmp_path)
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (signing / dk.PRIVATE_NAME).write_bytes(rsa_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with pytest.raises(SystemExit):
        dk.ensure(signing, verify, None)
