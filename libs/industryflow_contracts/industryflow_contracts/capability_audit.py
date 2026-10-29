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
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from . import tenancy

# The stream, in the capability store (ADR-0015 dec 7).
STREAM_KEY = "nbcap-audit"
# Bound on undrained events (dec 7): approximately trimmed by every append. The writer deletes what it
# has written, so the stream holds only the backlog; far above what one drain interval sees, and an
# append past it drops the oldest, which the backlog alert shows first.
STREAM_MAXLEN = 100_000

# Marks a handle's first use by a plane. Outlives any handle (the longest-lived is refreshed while its
# server runs); a handle used again after its marker expired is recorded once more, never missed.
FIRST_USE_PREFIX = "nbcap-audit-seen:"
FIRST_USE_MARKER_TTL_SECONDS = 7 * 24 * 3600

# A presented value with no entry is recorded once per plane and digest per window (dec 8): anyone can
# present one, and a repeat says nothing the first did not. A refusal naming a real entry is recorded
# every time; only a real handle can cause one.
UNKNOWN_REFUSAL_PREFIX = "nbcap-audit-refused:"
UNKNOWN_REFUSAL_WINDOW_SECONDS = 3600
# And at most this many distinct unknown values per plane and source address per minute (dec 9): a
# flood of distinct random values is the one thing the per-digest window does not bound. Requests are
# not throttled; only what is recorded of them is.
SOURCE_LIMIT_PREFIX = "nbcap-audit-source:"
SOURCE_LIMIT = 60
SOURCE_WINDOW_SECONDS = 60

# KEYS[1] the per-digest marker, KEYS[2] the per-source counter, KEYS[3] the stream; ARGV[1] the digest
# window, ARGV[2] the source limit, ARGV[3] the source window, ARGV[4] the stream bound, then the event's
# pairs. Returns 1 when recorded, 0 for a repeat in its window, -1 the first time a source exceeds its
# limit in a window (the plane logs it once), -2 for every later one.
UNKNOWN_REFUSAL_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
local n = redis.call('INCR', KEYS[2])
if n == 1 then
  redis.call('EXPIRE', KEYS[2], ARGV[3])
end
if n > tonumber(ARGV[2]) then
  if n == tonumber(ARGV[2]) + 1 then
    return -1
  end
  return -2
end
redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
redis.call('XADD', KEYS[3], 'MAXLEN', '~', ARGV[4], '*', unpack(ARGV, 5))
return 1
"""

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
_DIGEST = re.compile(r"^[0-9a-f]{16}$")
_MAX_DETAIL = 512

# KEYS[1] a marker, KEYS[2] the stream; ARGV[1] marker TTL, ARGV[2] stream bound, then the event's
# field/value pairs. The event is appended only when the marker was absent, atomically: a first use
# (dec 5), or the first refusal of an unknown value in its window (dec 8).
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


def unknown_refusal_key(digest: str, plane: str) -> str:
    return f"{UNKNOWN_REFUSAL_PREFIX}{plane}:{digest}"


def source_limit_key(plane: str, source: str) -> str:
    return f"{SOURCE_LIMIT_PREFIX}{plane}:{source}"


def refusal(*, plane: str, handle: Any, reason: str, facts: Mapping[str, str],
            source: Optional[str]) -> tuple:
    """How a plane records a refused presented handle (dec 1, 2, 8, 9): ``("once", keys, args)`` for
    ``UNKNOWN_REFUSAL_SCRIPT`` when the value named no entry (by digest, once per window, within the
    source's limit), else ``("append", xadd_kwargs)`` for a plain append that names the entry and
    carries no digest. ``source`` is the address the value came from, as the plane sees it."""
    if facts.get("handle_id") is None:
        digest = presented_digest(handle)
        fields = event(REFUSED, plane=plane, reason=reason, digest=digest, user=facts.get("user"),
                       company_id=facts.get("company_id"), audience=facts.get("audience"),
                       detail=f"source={source}" if source else None)
        keys = [unknown_refusal_key(digest, plane), source_limit_key(plane, source or "unknown"), STREAM_KEY]
        return "once", keys, [str(UNKNOWN_REFUSAL_WINDOW_SECONDS), str(SOURCE_LIMIT),
                              str(SOURCE_WINDOW_SECONDS), str(STREAM_MAXLEN), *flat(fields)]
    fields = event(REFUSED, plane=plane, reason=reason, user=facts.get("user"),
                   company_id=facts.get("company_id"), audience=facts.get("audience"),
                   handle_id=facts["handle_id"])
    return ("append", append_args(fields))


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
    if digest is not None and not _DIGEST.match(str(digest)):
        raise ValueError("a digest is presented_digest()'s 16 hex characters")
    for name, value in (("user", user), ("audience", audience), ("digest", digest),
                        ("reason", reason), ("detail", detail)):
        if value is not None:
            fields[name] = _text(value)
    return fields


def _text(value: Any) -> str:
    """A text column's value: bounded, and without the NUL the database's text type refuses, so a
    name a client chose can neither fail the request that records it nor stall the writer."""
    return str(value).replace("\x00", "\ufffd")[:_MAX_DETAIL]


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
        value = fields.get(name)
        out[name] = _text(value) if value is not None and name in ("user", "reason", "detail") else value
    if out["company_id"] is not None:
        out["company_id"] = tenancy.canonical_company_id(out["company_id"])
    if out["handle_id"] is not None:
        out["handle_id"] = str(uuid.UUID(out["handle_id"]))
    if out["audience"] is not None and out["audience"] not in AUDIENCES:
        raise ValueError(f"unknown audience {out['audience']!r}")
    if out["digest"] is not None and not _DIGEST.match(out["digest"]):
        raise ValueError("not a presented-handle digest")
    return out
