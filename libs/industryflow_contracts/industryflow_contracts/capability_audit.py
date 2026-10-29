# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The capability-use record's event contract (ADR-0042).

Every plane that mints, resolves or revokes a capability appends an event to one stream in the
capability store, in the same atomic step as the store change the event describes (dec 3): a mint or
revocation in one MULTI with its event, a first use through ``FIRST_USE_SCRIPT``, which records the
first use of a handle by a plane once (dec 5). One trusted writer drains the stream into the platform
database (dec 4) and is the only reader of it.

A handle never enters an event: a record carries the handle's ``handle_id``, a non-secret identifier
bound into its store entry at mint (dec 2), and a presented handle with no entry carries
``presented_digest``.

Standard library only: this is imported by every service image.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from . import tenancy

# The stream, in the capability store (ADR-0015 dec 7).
STREAM_KEY = "nbcap-audit"
# Bound on undrained events (dec 7): approximately trimmed by every append. Far above what one drain
# interval sees; an append past it drops the oldest, and the writer's lag metric shows the backlog first.
STREAM_MAXLEN = 200_000

# Marks a handle's first use by a plane. Outlives any handle (the longest-lived is refreshed while its
# server runs); a handle used again after its marker expired is recorded once more, never missed.
FIRST_USE_PREFIX = "nbcap-audit-seen:"
FIRST_USE_MARKER_TTL_SECONDS = 7 * 24 * 3600

MINTED = "minted"
FIRST_USE = "first_use"
REFUSED = "refused"
REVOKED = "revoked"
CONSUMED = "consumed"
ENVIRONMENT_STARTED = "environment_started"
ENVIRONMENT_STOPPED = "environment_stopped"
SQL_SESSION_OPENED = "sql_session_opened"
SQL_SESSION_CLOSED = "sql_session_closed"
KINDS = (MINTED, FIRST_USE, REFUSED, REVOKED, CONSUMED, ENVIRONMENT_STARTED, ENVIRONMENT_STOPPED,
         SQL_SESSION_OPENED, SQL_SESSION_CLOSED)

# Who recorded it: the minters and the planes that resolve handles.
PLANES = ("hub", "ml_service", "api_gateway", "sql_proxy", "tracking_gateway", "cold_broker",
          "tenant_delete")

AUDIENCES = ("api", "sql", "tracking", "cold", "upload")

# 16 hex characters: enough to correlate repeated refusals of one presented value, far too few to
# search the handle space with.
_DIGEST_HEX = 16
_MAX_DETAIL = 512

# KEYS[1] the first-use marker, KEYS[2] the stream; ARGV[1] marker TTL, ARGV[2] stream bound, then the
# event's field/value pairs. The event is appended only when the marker was absent, atomically.
FIRST_USE_SCRIPT = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[2], '*', unpack(ARGV, 3))
  return 1
end
return 0
"""

# KEYS[1] a handle's store key, KEYS[2] the stream; ARGV[1] stream bound, then the event's pairs. A
# revocation or a consumption is recorded only when it removed the entry, atomically with the removal:
# an entry that expired on its own was not revoked by anyone. Returns how many keys were removed.
DELETE_RECORDED_SCRIPT = """
local removed = redis.call('DEL', KEYS[1])
if removed == 1 then
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[1], '*', unpack(ARGV, 2))
end
return removed
"""


def new_handle_id() -> str:
    """The non-secret identifier a minter binds into a new handle's entry (dec 2)."""
    return str(uuid.uuid4())


def presented_digest(handle: Any) -> str:
    """A short digest of a presented value that resolved to no entry (dec 2)."""
    raw = handle if isinstance(handle, (bytes, bytearray)) else str(handle or "").encode()
    return hashlib.sha256(raw).hexdigest()[:_DIGEST_HEX]


def first_use_key(handle_id: str, plane: str) -> str:
    return f"{FIRST_USE_PREFIX}{plane}:{handle_id}"


def event(kind: str, *, plane: str, user: Optional[str] = None, company_id: Optional[str] = None,
          audience: Optional[str] = None, handle_id: Optional[str] = None,
          digest: Optional[str] = None, reason: Optional[str] = None,
          detail: Optional[str] = None, at: Optional[datetime] = None) -> Dict[str, str]:
    """One event's stream fields. Absent values are left out; ``ValueError`` on a malformed one, so a
    plane cannot append what the writer would refuse."""
    if kind not in KINDS:
        raise ValueError(f"unknown capability event kind {kind!r}")
    if plane not in PLANES:
        raise ValueError(f"unknown recording plane {plane!r}")
    if audience is not None and audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}")
    fields = {"kind": kind, "plane": plane,
              "at": (at or datetime.now(timezone.utc)).isoformat()}
    if company_id is not None:
        fields["company_id"] = tenancy.canonical_company_id(company_id)
    if handle_id is not None:
        fields["handle_id"] = str(uuid.UUID(str(handle_id)))
    for name, value in (("user", user), ("audience", audience), ("digest", digest),
                        ("reason", reason), ("detail", detail)):
        if value is not None:
            fields[name] = str(value)[:_MAX_DETAIL]
    return fields


def flat(fields: Mapping[str, str]) -> List[str]:
    """Field/value pairs flattened, as XADD and ``FIRST_USE_SCRIPT`` take them."""
    out: List[str] = []
    for k, v in fields.items():
        out += [k, v]
    return out


def first_use_script_args(fields: Mapping[str, str]) -> tuple:
    """``(keys, args)`` for ``FIRST_USE_SCRIPT`` recording ``fields`` (a ``first_use`` event)."""
    if fields.get("kind") != FIRST_USE or "handle_id" not in fields:
        raise ValueError("a first-use record needs a first_use event with a handle_id")
    keys = [first_use_key(fields["handle_id"], fields["plane"]), STREAM_KEY]
    return keys, [str(FIRST_USE_MARKER_TTL_SECONDS), str(STREAM_MAXLEN), *flat(fields)]


def delete_recorded_args(store_key: str, fields: Mapping[str, str]) -> tuple:
    """``(keys, args)`` for ``DELETE_RECORDED_SCRIPT``: remove ``store_key``, recording ``fields`` (a
    ``revoked`` or ``consumed`` event) if it was there."""
    if fields.get("kind") not in (REVOKED, CONSUMED):
        raise ValueError("a removal is recorded as revoked or consumed")
    return [store_key, STREAM_KEY], [str(STREAM_MAXLEN), *flat(fields)]


def append_args(fields: Mapping[str, str]) -> dict:
    """Keyword arguments for a plain XADD of ``fields`` (redis-py ``xadd``), bounded like every append."""
    return {"name": STREAM_KEY, "fields": dict(fields), "maxlen": STREAM_MAXLEN, "approximate": True}


def decode(raw: Mapping[Any, Any]) -> Dict[str, Optional[str]]:
    """The writer's reading of one stream entry: every column, ``None`` where absent; ``ValueError``
    when the entry is not an event this contract produces."""
    def s(v):
        return v.decode() if isinstance(v, (bytes, bytearray)) else v

    fields = {s(k): s(v) for k, v in raw.items()}
    kind, plane = fields.get("kind"), fields.get("plane")
    if kind not in KINDS or plane not in PLANES:
        raise ValueError(f"not a capability event: kind={kind!r} plane={plane!r}")
    at = datetime.fromisoformat(fields["at"])
    if at.tzinfo is None:
        raise ValueError("event time has no zone")
    out: Dict[str, Optional[str]] = {"kind": kind, "plane": plane, "at": at.isoformat()}
    for name in ("user", "company_id", "audience", "handle_id", "digest", "reason", "detail"):
        out[name] = fields.get(name)
    if out["company_id"] is not None:
        out["company_id"] = tenancy.canonical_company_id(out["company_id"])
    if out["handle_id"] is not None:
        out["handle_id"] = str(uuid.UUID(out["handle_id"]))
    if out["audience"] is not None and out["audience"] not in AUDIENCES:
        raise ValueError(f"unknown audience {out['audience']!r}")
    return out
