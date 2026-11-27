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

A message may also carry the tenant's quota overrides (``caps``, ADR-0049 dec 2): a cap the message
does not name takes the platform default, so a consumer that forgets a stale status also returns that
tenant to the defaults. And the stored-data caps the tenant is over (``over``, dec 12), whose demand
is refused; a consumer that forgets a stale status refuses nothing for them.

Standard library only: this is imported by every service image.
"""
from __future__ import annotations

import json

from . import quotas, tenancy

# The topic, unless a deployment names another (KAFKA_TOPIC_TENANT_STATUS, the same for both sides).
DEFAULT_TOPIC = "tenant-status"

# How often the publisher sends the whole registry, and how long a consumer trusts what it heard.
PUBLISH_INTERVAL_SECONDS = 10
STALE_AFTER_SECONDS = 6 * PUBLISH_INTERVAL_SECONDS


def encode(company_id, active: bool, caps=None, over=()) -> tuple[bytes, bytes]:
    """The (key, value) of one tenant's status message. The key is the canonical company_id; ``caps``
    maps a cap to the tenant's override, and is left out when there is none; ``over`` names the
    stored-data caps the tenant is over, left out when none."""
    canonical = tenancy.canonical_company_id(company_id)
    if not isinstance(active, bool):
        raise ValueError("active must be a bool")
    body = {"company_id": canonical, "active": active}
    if caps:
        body["caps"] = {quotas.check_cap(cap): quotas.check_value(value) for cap, value in caps.items()}
    if over:
        stored = set(quotas.STORED_CAPS.values())
        body["over"] = sorted(cap for cap in over if quotas.check_cap(cap) in stored)
    return canonical.encode(), json.dumps(body).encode()


def decode_message(value: bytes) -> tuple[str, bool, dict, frozenset]:
    """``(canonical company_id, active, caps, over)`` from a message value; ``ValueError`` if it is not
    one. A cap or value this version does not know is dropped, not refused: the tenant takes its default,
    and an ``over`` this version does not know refuses nothing."""
    try:
        body = json.loads(value)
    except (TypeError, ValueError) as e:
        raise ValueError("not a tenant-status message") from e
    if not isinstance(body, dict) or not isinstance(body.get("active"), bool):
        raise ValueError("not a tenant-status message")
    caps = {}
    raw = body.get("caps")
    if isinstance(raw, dict):
        for cap, val in raw.items():
            try:
                caps[quotas.check_cap(cap)] = quotas.check_value(val)
            except ValueError:
                continue
    stored = set(quotas.STORED_CAPS.values())
    over = body.get("over")
    over = frozenset(c for c in over if c in stored) if isinstance(over, list) else frozenset()
    return tenancy.canonical_company_id(body.get("company_id")), body["active"], caps, over


def decode(value: bytes) -> tuple[str, bool]:
    """``(canonical company_id, active)`` from a message value; ``ValueError`` if it is not one."""
    company_id, active, _caps, _over = decode_message(value)
    return company_id, active
