# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tenant-status stream: which tenants ingestion must refuse (ADR-0033 dec 3).

Ingestion reaches only the broker (ADR-0002 dec 3), so the registry's view of each tenant is carried
to it there. The publisher sends every registered tenant's status at a short, fixed interval, so a
consumer that starts from the newest offset knows every tenant within one interval and no retention
or compaction setting matters. A status a consumer has not heard again for ``STALE_AFTER_SECONDS`` is
forgotten: the stream sheds a suspended tenant's load, and the writers of tenant data, which read the
registry itself, are what enforce suspension.

Standard library only: this is imported by every service image.
"""
from __future__ import annotations

import json

from . import tenancy

# The topic, unless a deployment names another (KAFKA_TOPIC_TENANT_STATUS, the same for both sides).
DEFAULT_TOPIC = "tenant-status"

# How often the publisher sends the whole registry, and how long a consumer trusts what it heard.
PUBLISH_INTERVAL_SECONDS = 10
STALE_AFTER_SECONDS = 6 * PUBLISH_INTERVAL_SECONDS


def encode(company_id, active: bool) -> tuple[bytes, bytes]:
    """The (key, value) of one tenant's status message. The key is the canonical company_id."""
    canonical = tenancy.canonical_company_id(company_id)
    if not isinstance(active, bool):
        raise ValueError("active must be a bool")
    return canonical.encode(), json.dumps({"company_id": canonical, "active": active}).encode()


def decode(value: bytes) -> tuple[str, bool]:
    """``(canonical company_id, active)`` from a message value; ``ValueError`` if it is not one."""
    try:
        body = json.loads(value)
    except (TypeError, ValueError) as e:
        raise ValueError("not a tenant-status message") from e
    if not isinstance(body, dict) or not isinstance(body.get("active"), bool):
        raise ValueError("not a tenant-status message")
    return tenancy.canonical_company_id(body.get("company_id")), body["active"]
