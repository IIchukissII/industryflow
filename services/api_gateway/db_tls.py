# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TLS for connections to TimescaleDB (ADR-0017), shared by the gateway and its admin scripts.

Kept free of the gateway's settings so scripts/manage_users.py and scripts/seed_users.py, which
connect as the same role, use exactly the same verification without needing the app's config.
"""
import os
import ssl
from typing import Optional


def db_ssl_context() -> Optional[ssl.SSLContext]:
    """SSLContext per DB_SSLMODE. Default verify-full: encrypt and verify the server certificate
    against DB_SSLROOTCERT (the internal CA) with hostname checking."""
    mode = (os.getenv("DB_SSLMODE") or "verify-full").lower()
    if mode in ("disable", "allow", ""):
        return None
    ca = os.getenv("DB_SSLROOTCERT") or None
    if ca and not os.path.exists(ca):
        ca = None
    ctx = ssl.create_default_context(cafile=ca)
    if mode in ("require", "prefer"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        ctx.check_hostname = False
    return ctx  # verify-full uses create_default_context defaults (CERT_REQUIRED + hostname)
