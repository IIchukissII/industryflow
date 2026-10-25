# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
scripts/device-ca.sh revokes a device, not a file (ADR-0007 dec 4-6).

Runs the real script against a throwaway CA and checks the result the edge acts on: what
``openssl verify -crl_check_all`` says about each issued certificate against the published
chain and CRL bundle. A renewal must not survive its device's revocation, and revoking one
tenant's device must not touch another tenant's device with the same device_id.
"""
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "device-ca.sh"

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")


@pytest.fixture(scope="module")
def ca(tmp_path_factory):
    root = tmp_path_factory.mktemp("device-ca")

    def run(*args, check=True):
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            env={**os.environ, "DEVICE_CA_DIR": str(root)},
            capture_output=True,
            text=True,
            check=check,
        )

    run("init")
    return root, run


def _serial(out: str) -> str:
    return out.split("serial=")[1].split()[0]


def _verify(root: Path, cert: Path) -> bool:
    r = subprocess.run(
        ["openssl", "verify", "-crl_check_all",
         "-CAfile", str(root / "ca-chain.crt"),
         "-CRLfile", str(root / "crl" / "device-ca.crl"),
         str(cert)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def test_issue_renew_revoke_cross_tenant(ca):
    root, run = ca
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())

    first = _serial(run("issue", tenant_a, "gw-1").stdout)
    renewed = _serial(run("issue", tenant_a, "gw-1").stdout)
    other_tenant = _serial(run("issue", tenant_b, "gw-1").stdout)
    # A device_id that merely starts with the revoked one is a different device.
    prefix_sibling = _serial(run("issue", tenant_a, "gw-10").stdout)

    a_dir = root / "devices" / tenant_a / "gw-1"
    b_dir = root / "devices" / tenant_b / "gw-1"
    # Stored per tenant and serial: the renewal did not overwrite the first certificate.
    assert first != renewed
    for d, s in ((a_dir, first), (a_dir, renewed), (b_dir, other_tenant)):
        assert (d / f"{s}.crt").is_file()
        assert (d / f"{s}.chain.crt").is_file()
        assert (d / f"{s}.key").is_file()
    assert os.readlink(a_dir / "current.crt") == f"{renewed}.crt"

    certs = {
        "first": a_dir / f"{first}.crt",
        "renewed": a_dir / f"{renewed}.crt",
        "other_tenant": b_dir / f"{other_tenant}.crt",
        "prefix_sibling": root / "devices" / tenant_a / "gw-10" / f"{prefix_sibling}.crt",
    }
    assert all(_verify(root, c) for c in certs.values())

    out = run("revoke", tenant_a, "gw-1").stdout
    assert "revoked: 2 certificate(s)" in out

    assert not _verify(root, certs["first"])
    assert not _verify(root, certs["renewed"])
    assert _verify(root, certs["other_tenant"])
    assert _verify(root, certs["prefix_sibling"])

    index = (root / "intermediate" / "index.txt").read_text().splitlines()
    status = {line.split("\t")[3]: line.split("\t")[0] for line in index}
    assert status[first] == "R" and status[renewed] == "R"
    assert status[other_tenant] == "V" and status[prefix_sibling] == "V"


def test_revoke_tenant_revokes_every_device_of_that_tenant_only(ca):
    root, run = ca
    gone, kept = str(uuid.uuid4()), str(uuid.uuid4())
    certs = {}
    for tenant, device in ((gone, "gw-1"), (gone, "gw-1"), (gone, "line-2"), (kept, "gw-1")):
        serial = _serial(run("issue", tenant, device).stdout)
        certs[serial] = (tenant, root / "devices" / tenant / device / f"{serial}.crt")
    assert all(_verify(root, c) for _, c in certs.values())

    # The tenant is canonicalized, so an upper-case request names the same tenant.
    out = run("revoke-tenant", gone.upper()).stdout
    assert "revoked: 3 certificate(s)" in out
    for tenant, cert in certs.values():
        assert _verify(root, cert) == (tenant == kept)

    # Idempotent: nothing left to revoke is a success, and the CRL is still re-signed.
    again = run("revoke-tenant", gone)
    assert "revoked: 0 certificate(s)" in again.stdout
    assert all(_verify(root, c) == (t == kept) for t, c in certs.values())


def test_revoke_tenant_needs_a_uuid(ca):
    _, run = ca
    for args in ((), ("not-a-uuid",)):
        r = run("revoke-tenant", *args, check=False)
        assert r.returncode != 0


def test_revoke_needs_the_tenant(ca):
    _, run = ca
    r = run("revoke", "gw-1", check=False)
    assert r.returncode != 0
    assert "usage" in r.stderr


def test_revoke_of_unknown_device_fails(ca):
    _, run = ca
    r = run("revoke", str(uuid.uuid4()), "never-issued", check=False)
    assert r.returncode != 0
    assert "no valid certificate" in r.stderr


def test_ci_runs_this_file_when_the_script_changes():
    # This proof is only worth something if CI runs it: the matrix must name this file, and the
    # path filter must fire on both the script and the test.
    wf = (SCRIPT.parents[1] / ".github" / "workflows" / "unit-tests.yml").read_text()
    here = "scripts/tests/test_device_ca.py"
    assert f"path: {here}" in wf
    assert "- 'scripts/device-ca.sh'" in wf
    assert f"- '{here}'" in wf


@pytest.mark.parametrize("device", ["../escape", "a/b", ".hidden", "x y", ""])
def test_unsafe_device_id_is_refused(ca, device):
    _, run = ca
    r = run("issue", str(uuid.uuid4()), device, check=False)
    assert r.returncode != 0


@pytest.fixture
def fresh_ca(tmp_path):
    def run(*args, check=True, **env):
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            env={**os.environ, "DEVICE_CA_DIR": str(tmp_path), **env},
            capture_output=True,
            text=True,
            check=check,
        )

    run("init")
    return tmp_path, run


def test_check_certs_raises_a_device_whose_renewal_is_owed(fresh_ca):
    # ADR-0007 dec 5: the check fails on a device's NEWEST unrevoked certificate only. A device
    # renewed in time, or revoked, is not owed anything.
    root, run = fresh_ca
    tenant = str(uuid.uuid4())
    run("issue", tenant, "owed", DEVICE_DAYS="5")
    run("issue", tenant, "renewed", DEVICE_DAYS="5")
    run("issue", tenant, "renewed")
    run("issue", tenant, "revoked", DEVICE_DAYS="5")
    run("revoke", tenant, "revoked")
    run("issue", tenant, "fine")

    r = run("check-certs", check=False)
    assert r.returncode == 1
    assert f"EXPIRING: device {tenant}/owed " in r.stderr
    for device in ("renewed", "revoked", "fine"):
        assert f"/{device} " not in r.stderr + r.stdout
    assert "ok: 2 device(s) valid beyond 14d" in r.stdout

    run("issue", tenant, "owed")
    assert run("check-certs").returncode == 0


def test_check_certs_reports_an_expired_device_without_failing(fresh_ca):
    # The edge already refuses it; revocation is what decommissions it.
    root, run = fresh_ca
    tenant = str(uuid.uuid4())
    serial = _serial(run("issue", tenant, "gone").stdout)
    index = root / "intermediate" / "index.txt"
    rows = [line.split("\t") for line in index.read_text().splitlines()]
    for row in rows:
        if row[3] == serial:
            row[1] = "200101000000Z"
    index.write_text("".join("\t".join(row) + "\n" for row in rows))
    run("crl")

    r = run("check-certs")
    assert f"expired: device {tenant}/gone (2020-01-01 00:00:00 UTC)" in r.stdout


def test_check_certs_refuses_a_register_it_cannot_trust(fresh_ca):
    root, run = fresh_ca
    run("issue", str(uuid.uuid4()), "gw-1", DEVICE_DAYS="5")
    reg = root / "crl" / "issued.tsv"
    signed = reg.read_bytes()

    # Hiding the expiring device breaks the signature.
    reg.write_text("\n".join(reg.read_text().splitlines()[:2]) + "\n")
    r = run("check-certs", check=False)
    assert r.returncode != 0
    assert "signature does not verify" in r.stderr

    # A genuine but older register does not describe the published CRL.
    sig = reg.with_name("issued.tsv.sig")
    old_sig = sig.read_bytes()
    run("crl")
    reg.write_bytes(signed)
    sig.write_bytes(old_sig)
    r = run("check-certs", check=False)
    assert r.returncode != 0
    assert "republish both together" in r.stderr


def test_check_raises_a_ca_certificate_close_to_expiry(tmp_path):
    # An expiring intermediate refuses every device, like an expired CRL.
    env = {**os.environ, "DEVICE_CA_DIR": str(tmp_path)}
    subprocess.run(["sh", str(SCRIPT), "init"], env={**env, "INT_DAYS": "30"},
                   capture_output=True, check=True)
    r = subprocess.run(["sh", str(SCRIPT), "check"], env=env, capture_output=True, text=True)
    assert r.returncode == 1
    assert "STALE: CA certificate CN=IndustryFlow Device Issuing CA" in r.stderr
    ok = subprocess.run(["sh", str(SCRIPT), "check"], env={**env, "CA_CERT_WARN_DAYS": "7"},
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
