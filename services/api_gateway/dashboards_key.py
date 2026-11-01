# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The dashboards' sign-on key (ADR-0016 dec 7): generated once, its public half derived every run.

Run by the `dashboards-key-init` one-shot from this image, as root, with no network: the private key
is created only when absent and is handed to the gateway's user; the public key is always derived
from it, so the two halves can never disagree, whatever an earlier run or an operator left behind.
Each file is written beside its target and renamed into place, so a run that dies leaves either the
old file or the new one, never half of one.

    python dashboards_key.py <signing-dir> <verify-dir> <gateway-uid>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

PRIVATE_NAME = "signing.pem"
PUBLIC_NAME = "public.pem"


def _replace(path: Path, data: bytes, mode: int, owner: int | None = None) -> None:
    tmp = path.with_name(path.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if owner is not None:
        os.chown(tmp, owner, owner)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def ensure(signing_dir: Path, verify_dir: Path, gateway_uid: int | None) -> str:
    private_path, public_path = signing_dir / PRIVATE_NAME, verify_dir / PUBLIC_NAME
    outcome = "kept"
    if private_path.exists() and private_path.stat().st_size:
        key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        outcome = "generated"
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise SystemExit(f"{private_path} is not a P-256 key; remove it to generate a new one")
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    # The public half first: a run that dies before the private half lands leaves the old pair whole.
    if not public_path.exists() or public_path.read_bytes() != public:
        _replace(public_path, public, 0o644)
        outcome = outcome if outcome == "generated" else "public half re-derived"
    if outcome == "generated":
        private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        _replace(private_path, private, 0o400, gateway_uid)
    elif gateway_uid is not None:
        os.chown(private_path, gateway_uid, gateway_uid)
        os.chmod(private_path, 0o400)
    return outcome


if __name__ == "__main__":
    signing, verify, uid = sys.argv[1:4]
    print(f"dashboards signing key: {ensure(Path(signing), Path(verify), int(uid))}")
