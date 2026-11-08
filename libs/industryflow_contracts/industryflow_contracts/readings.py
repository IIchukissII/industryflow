# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A device's reading, as a device sends it to the ingest edge and ingestion accepts it (ADR-0002).

Ingestion validates the body against this shape; the stream applies the same clock-skew bound to what
reaches the topic another way. A device names its tenant only through its certificate, never in the
body (ADR-0002 dec 3), so the body has no tenant field.
"""
from __future__ import annotations

import ssl
from typing import Any, Dict, Optional

# How far ahead of the receiving clock a reading's timestamp may be. A reading stamped further in the
# future is a device clock fault: on the stream it would drag the aggregation watermark ahead and
# close every live window early. Ingestion refuses it at the edge; the stream dead-letters it.
MAX_FUTURE_SKEW_SECONDS = 300

# The body's fields: required ones, then the optional ones a device may add.
REQUIRED_FIELDS = ("timestamp", "sensor_id", "equipment_id", "site_id", "value")
OPTIONAL_FIELDS = ("unit", "quality_code")


def reading(*, timestamp: str, sensor_id: str, equipment_id: str, site_id: str, value: float,
            unit: Optional[str] = None, quality_code: Optional[int] = None) -> Dict[str, Any]:
    """One reading's body. ``timestamp`` is ISO 8601 with an offset; an optional field left out is
    not sent."""
    body: Dict[str, Any] = {"timestamp": timestamp, "sensor_id": str(sensor_id),
                            "equipment_id": str(equipment_id), "site_id": site_id, "value": float(value)}
    if unit is not None:
        body["unit"] = unit
    if quality_code is not None:
        body["quality_code"] = quality_code
    return body


def device_tls_context(cert: str, key: str, ca: str) -> ssl.SSLContext:
    """The device's side of mutual TLS: it presents its certificate (``scripts/device-ca.sh``) and
    verifies the edge's against ``ca``, host name included (ADR-0002 dec 1, 2)."""
    context = ssl.create_default_context(cafile=ca)
    context.load_cert_chain(cert, key)
    return context
