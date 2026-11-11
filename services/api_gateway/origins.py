# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which browser origins the gateway admits (ADR-0004 dec 6, 9; ADR-0048 dec 2).

A browser sends the platform's cookies on every same-site request, and the notebook origin is the
same site, so a cookie alone says nothing about which page sent a request. An origin is admitted when
it is in the configured allowlist (``CORS_ORIGINS``) or is exactly the origin the request was addressed
to (same origin behind an edge that forwards Host). The notebook origin is neither (``config``
refuses it in the allowlist, and it is refused here by name, whatever host a request is addressed to),
so script there can open no socket and send no state-changing request.
"""
from typing import Optional

from config import cors_origin_list, get_settings


def allowed_origins() -> set[str]:
    """The gateway's configured origin allowlist (CORS_ORIGINS, ADR-0004 dec 6)."""
    return {o.rstrip("/").lower() for o in cors_origin_list(get_settings().CORS_ORIGINS)}


def origin_admitted(origin: Optional[str], host: Optional[str]) -> bool:
    """Whether a browser request from ``origin`` addressed to ``host`` is admitted. A request without
    an Origin is not sent by a browser on another origin's behalf, so no ambient cookie of a victim is
    involved; it is admitted here and still needs its own credential."""
    if origin is None:
        return True
    origin = origin.rstrip("/").lower()
    notebook = get_settings().NOTEBOOK_ORIGIN
    if notebook and origin == notebook.lower():
        return False
    if origin in allowed_origins():
        return True
    return bool(host) and origin in (f"https://{host.lower()}", f"http://{host.lower()}")
