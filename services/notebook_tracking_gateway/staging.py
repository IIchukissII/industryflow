# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an uploaded artifact rests while it is judged (ADR-0030 dec 4, Consequences rev 1) — the
**pure** key rules, no I/O.

An uploaded artifact is staged, then admitted. That order is forced: the byte half of the structural
rule needs the bytes, and ADR-0019 dec 6 keeps the mediator out of the artifact data path so it
scales on request rate rather than artifact size — and ADR-0030 observes that these artifacts are
the largest the platform will see. Both hold only if the bytes come to rest somewhere before they are
judged. "Refused at the gate" therefore means refused **before it is a model**, not before it exists.

That makes the staging area's location a tenancy decision rather than a naming one, and it has two
requirements that pull against each other:

  * **no tenant may address it.** The artifact plane forces every key under the caller's
    ``tenant_<uuid>/`` prefix (``policy.scope_artifact_key``), so a key rooted anywhere else is
    unreachable through it — which is exactly why staging is rooted outside every tenant prefix
    rather than inside the uploader's own. Inside, a tenant could read, overwrite, or list its own
    unadmitted bytes through the ordinary artifact plane, and MLflow would see them sitting among
    real artifacts.
  * **it is still per-tenant.** Unaddressable is not the same as unpartitioned. One tenant's staged
    bytes must never share a path with another's, or the admitted copy could be sourced from a
    neighbour's upload — the boundary has to hold in the staging area too, not only after promotion.

So: staging is rooted outside all tenant prefixes, and partitioned by tenant within it.

Promotion is a copy into the tenant's own prefix under a path that says where the artifact came
from. Nothing writes into that prefix until the artifact is admitted, which is the property the whole
staged design exists to keep.

**Member paths are caller-supplied and therefore hostile.** Object keys are opaque strings — a
``..`` in one is a literal character sequence, not a parent directory — so a traversal here does not
escape the way it would on a filesystem. That is not a reason to accept one: these keys are joined,
compared against prefixes, and may be handed to any store implementation that maps keys onto paths.
A path that cannot be reasoned about is refused instead.
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

# Rooted outside every tenant prefix, so the artifact plane cannot reach it: that plane rewrites any
# key to sit under `tenant_<uuid>/`, which this never does. The name is deliberately not a valid
# tenant token.
STAGING_ROOT = "_upload-staging/"

# Where an admitted artifact lands inside the tenant's own prefix. Separate from the kernel's
# run-scoped artifact paths because provenance is a fact, not a decoration (ADR-0030 dec 8): an
# uploaded artifact is not pretending to be the output of a run it never had.
ADMITTED_ROOT = "uploads/"

_UPLOAD_ID_BYTES = 16
_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# One artifact's worth of files. A bound the mediator can hold in mind, not a judgement about
# frameworks: an artifact with thousands of members is not one this rule can meaningfully read the
# heads of, and an unbounded list is a way to make it try.
MAX_MEMBERS = 64

_UNSAFE_SEGMENTS = {"", ".", ".."}


def new_upload_id() -> str:
    """An opaque id for one upload. High-entropy so it is not guessable: a staged upload is
    unadmitted bytes, and an id that could be guessed would let one caller commit another's."""
    return secrets.token_urlsafe(_UPLOAD_ID_BYTES)


def is_upload_id(value: str) -> bool:
    return bool(value) and bool(_UPLOAD_ID_RE.match(value))


def safe_member_path(name: str) -> Optional[str]:
    """A caller-supplied member path, normalised, or ``None`` if it cannot be reasoned about.

    Refuses rather than sanitises. Silently rewriting a hostile path into a safe one stores the
    caller's bytes somewhere they did not ask for and calls it success; a refusal says what happened.
    """
    if not name or not isinstance(name, str):
        return None
    if len(name) > 512:
        return None
    if name.startswith("/") or name.startswith("\\"):
        return None            # absolute: the caller does not choose the root
    if "\\" in name:
        return None            # one separator, so one reading of the path
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return None            # control characters have no place in a key
    segments = name.split("/")
    if any(seg in _UNSAFE_SEGMENTS for seg in segments):
        return None            # '', '.', '..' -> empty, redundant, or traversal
    return "/".join(segments)


def staging_key(company_id_token: str, upload_id: str, member: str) -> str:
    """Where one member of an unadmitted artifact rests.

    ``company_id_token`` is the caller's already-validated tenant token (``policy.tenant_prefix``'s
    basis) — this module never derives a tenant, so it cannot get one wrong; it places what it is
    given.
    """
    return f"{STAGING_ROOT}{company_id_token}/{upload_id}/{member}"


def staging_prefix(company_id_token: str, upload_id: str) -> str:
    """Everything staged for one upload — what is listed to read, and deleted to abandon."""
    return f"{STAGING_ROOT}{company_id_token}/{upload_id}/"


def admitted_key(artifact_prefix: str, upload_id: str, member: str) -> str:
    """Where an admitted member lands: inside the tenant's own prefix, under a path that says it was
    uploaded. ``artifact_prefix`` is ``policy.artifact_prefix(company_id)`` — again given, not
    derived."""
    return f"{artifact_prefix}{ADMITTED_ROOT}{upload_id}/{member}"


def admitted_uri(bucket: str, artifact_prefix: str, upload_id: str) -> str:
    """The artifact location an admitted upload is addressed by, for the registration gate to carry
    as the model version's source."""
    return f"s3://{bucket}/{artifact_prefix}{ADMITTED_ROOT}{upload_id}"
