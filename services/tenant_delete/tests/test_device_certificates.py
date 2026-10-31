# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A deleted tenant's device certificates are recorded only once each unexpired one is revoked
(ADR-0033 dec 4). Runs the real scripts/device-ca.sh against a throwaway CA and verifies its
published files, as the job reads them.
"""
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tenant_delete.device_certificates import DeviceCaError, DeviceCertificates

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "device-ca.sh"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")


class CA:
    def __init__(self, root: Path):
        self.root = root
        self.run("init")

    def run(self, *args, **env):
        return subprocess.run(["sh", str(SCRIPT), *args], check=True, capture_output=True, text=True,
                              env={**os.environ, "DEVICE_CA_DIR": str(self.root), **env}).stdout

    def step(self, **kw):
        return DeviceCertificates(self.root / "ca-chain.crt", self.root / "crl" / "device-ca.crl",
                                  self.root / "crl" / "issued.tsv", **kw)


@pytest.fixture
def ca(tmp_path):
    return CA(tmp_path / "ca")


def _before():
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def test_a_tenant_with_no_certificate_is_recorded(ca):
    removal = ca.step().verify(str(uuid.uuid4()), [], _before())
    assert removal.detail["revoked"] == 0 and removal.detail["expired"] == 0


def test_recorded_only_once_every_certificate_is_revoked_and_no_other_tenants(ca):
    gone, kept = str(uuid.uuid4()), str(uuid.uuid4())
    for tenant, device in ((gone, "gw-1"), (gone, "gw-1"), (gone, "line-2"), (kept, "gw-1")):
        ca.run("issue", tenant, device)
    requested = _before()

    assert ca.step().verify(gone, [], requested) is None

    ca.run("revoke-tenant", gone)
    removal = ca.step().verify(gone.upper(), [], requested)
    assert removal.detail["revoked"] == 3 and removal.detail["expired"] == 0
    assert len(removal.detail["register_sha256"]) == 64
    # The other tenant's device is still valid.
    assert ca.step().verify(kept, [], requested) is None


def test_a_crl_signed_before_the_request_is_waited_on(ca):
    tenant = str(uuid.uuid4())
    assert ca.step().verify(tenant, [], datetime.now(timezone.utc) + timedelta(minutes=5)) is None


def test_a_revocation_signed_in_the_second_of_the_request_is_accepted(ca):
    """On the box, the e2e teardown requested deletion at 15:50:58.263 and revoked at 15:50:58: the CRL
    read as signed before the request, and the deletion waited for the next re-signing."""
    from cryptography import x509
    tenant = str(uuid.uuid4())
    ca.run("issue", tenant, "gw-1")
    ca.run("revoke-tenant", tenant)
    signed = x509.load_pem_x509_crl((ca.root / "crl" / "intermediate.crl").read_bytes()).last_update_utc
    assert ca.step().verify(tenant, [], signed + timedelta(milliseconds=500)).detail["revoked"] == 1
    # A CRL signed in an earlier second than the request is still waited on.
    assert ca.step().verify(tenant, [], signed + timedelta(seconds=1)) is None


def test_an_expired_certificate_needs_no_revocation(ca):
    tenant = str(uuid.uuid4())
    ca.run("issue", tenant, "gw-1", DEVICE_DAYS="1")
    ca.run("crl")
    later = lambda: datetime.now(timezone.utc) + timedelta(days=2)  # noqa: E731 - the CRL lives 7 days
    removal = ca.step(clock=later).verify(tenant, [], _before())
    assert removal.detail == {**removal.detail, "revoked": 0, "expired": 1}


def test_a_register_of_another_crl_is_waited_on(ca, tmp_path):
    tenant = str(uuid.uuid4())
    ca.run("issue", tenant, "gw-1")
    stale = tmp_path / "stale"
    stale.mkdir()
    for name in ("issued.tsv", "issued.tsv.sig"):
        shutil.copy(ca.root / "crl" / name, stale / name)
    ca.run("revoke-tenant", tenant)
    step = DeviceCertificates(ca.root / "ca-chain.crt", ca.root / "crl" / "device-ca.crl", stale / "issued.tsv")
    assert step.verify(tenant, [], _before()) is None


def test_an_edited_register_is_refused(ca):
    tenant = str(uuid.uuid4())
    ca.run("issue", tenant, "gw-1")
    register = ca.root / "crl" / "issued.tsv"
    # Hiding the tenant's unrevoked certificate from the register.
    register.write_text("".join(l for l in register.read_text().splitlines(True) if tenant not in l))
    with pytest.raises(DeviceCaError, match="signature"):
        ca.step().verify(tenant, [], _before())


def test_files_of_another_ca_are_refused(ca, tmp_path):
    other = CA(tmp_path / "other")
    step = DeviceCertificates(other.root / "ca-chain.crt", ca.root / "crl" / "device-ca.crl",
                              ca.root / "crl" / "issued.tsv")
    with pytest.raises(DeviceCaError):
        step.verify(str(uuid.uuid4()), [], _before())
