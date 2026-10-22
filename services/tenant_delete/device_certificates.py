# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The device certificates of a deleted tenant: verified revoked (ADR-0033 dec 4, ADR-0007 dec 8).

Revocation happens where the issuing key lives, by the operator (``device-ca.sh revoke-tenant``);
this step only verifies it, from public files the edge also holds: the CA chain, the CRL bundle, and
the issued register the CA signs beside its CRL (every certificate it signed, with its tenant and
expiry). It records the store once every unexpired certificate of the tenant is on a CRL that is
valid, current, and signed after the deletion was requested. Until then the deletion waits on it.
The job holds no key and reaches the CA through nothing but these files.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from .job import Removal

logger = logging.getLogger(__name__)


class DeviceCaError(Exception):
    """The published CA files do not verify: a failed step, never a record."""


@dataclass(frozen=True)
class Issued:
    serial: int
    not_after: datetime
    tenant: str
    device: str


def _asn1_time(value: str) -> datetime:
    # index.txt keeps the certificate's own encoding: UTCTime (YYMMDDHHMMSSZ) or GeneralizedTime.
    fmt = "%y%m%d%H%M%SZ" if len(value) == 13 else "%Y%m%d%H%M%SZ"
    return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)


def parse_register(text: str) -> tuple[int, list]:
    """(the CRL number it names, every issued certificate)."""
    crl_number, issued = None, []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if fields[0] == "crl_number" and len(fields) == 2:
            crl_number = int(fields[1], 16)
        elif len(fields) == 4:
            issued.append(Issued(int(fields[0], 16), _asn1_time(fields[1]), fields[2].lower(), fields[3]))
        else:
            raise DeviceCaError(f"issued register: unreadable line {line!r}")
    if crl_number is None:
        raise DeviceCaError("issued register names no CRL")
    return crl_number, issued


class DeviceCertificates:
    def __init__(self, chain: Path, crl: Path, register: Path,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.chain, self.crl, self.register = Path(chain), Path(crl), Path(register)
        self.signature = self.register.with_name(self.register.name + ".sig")
        self.clock = clock

    def _issuing_ca(self) -> x509.Certificate:
        certs = x509.load_pem_x509_certificates(self.chain.read_bytes())
        roots = [c for c in certs if c.subject == c.issuer]
        issuing = [c for c in certs if c.subject != c.issuer]
        if len(roots) != 1 or len(issuing) != 1:
            raise DeviceCaError("CA chain is not one issuing intermediate and its root")
        try:
            issuing[0].verify_directly_issued_by(roots[0])
        except Exception as e:  # noqa: BLE001 - any failure is an unverified chain
            raise DeviceCaError("the issuing intermediate is not signed by the root") from e
        return issuing[0]

    def _issuing_crl(self, issuing: x509.Certificate) -> x509.CertificateRevocationList:
        # The bundle is the root's CRL and the intermediate's; devices are on the intermediate's.
        bundle = self.crl.read_bytes()
        pems = [b"-----BEGIN X509 CRL-----" + part for part in bundle.split(b"-----BEGIN X509 CRL-----")[1:]]
        crls = [x509.load_pem_x509_crl(p) for p in pems]
        mine = [c for c in crls if c.issuer == issuing.subject]
        if len(mine) != 1:
            raise DeviceCaError("the CRL bundle holds no single CRL of the issuing intermediate")
        if not mine[0].is_signature_valid(issuing.public_key()):
            raise DeviceCaError("the issuing intermediate's CRL signature does not verify")
        return mine[0]

    def _register(self, issuing: x509.Certificate) -> bytes:
        data = self.register.read_bytes()
        try:
            issuing.public_key().verify(self.signature.read_bytes(), data, padding.PKCS1v15(), hashes.SHA256())
        except Exception as e:  # noqa: BLE001 - any failure is an unsigned register
            raise DeviceCaError("the issued register's signature does not verify") from e
        return data

    def verify(self, company_id: str, user_ids: list, requested_at: datetime) -> Optional[Removal]:
        tenant = company_id.lower()
        issuing = self._issuing_ca()
        crl = self._issuing_crl(issuing)
        data = self._register(issuing)
        crl_number, issued = parse_register(data.decode())
        own_number = crl.extensions.get_extension_for_class(x509.CRLNumber).value.crl_number
        if crl_number != own_number:
            logger.warning("tenant %s: the issued register names CRL %s, the published CRL is %s; "
                           "publish them together", company_id, crl_number, own_number)
            return None
        now = self.clock()
        if crl.next_update_utc is None or crl.next_update_utc <= now:
            raise DeviceCaError(f"the issuing intermediate's CRL expired at {crl.next_update_utc}")
        if crl.last_update_utc < requested_at:
            logger.warning("tenant %s: the CRL was signed at %s, before the deletion was requested; "
                           "waiting for device-ca.sh revoke-tenant", company_id, crl.last_update_utc)
            return None
        revoked = {r.serial_number for r in crl}
        mine = [c for c in issued if c.tenant == tenant]
        unexpired = [c for c in mine if c.not_after > now]
        missing = sorted(f"{c.serial:X}" for c in unexpired if c.serial not in revoked)
        if missing:
            logger.warning("tenant %s: certificates %s are not revoked; waiting for device-ca.sh "
                           "revoke-tenant %s", company_id, missing, company_id)
            return None
        return Removal(detail={
            "revoked": len(unexpired),
            "expired": len(mine) - len(unexpired),
            "crl_number": own_number,
            "register_sha256": hashlib.sha256(data).hexdigest(),
        })
