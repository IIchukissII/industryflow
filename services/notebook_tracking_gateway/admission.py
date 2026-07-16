# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Structural admission of an externally-authored artifact (ADR-0030 dec 4) — the **pure** rule, no I/O.

The gateway is the exclusive write path to the artifact store, so it is where bytes can still be
refused before they reach the tenant's namespace (ADR-0030 dec 2 rev 1). What it refuses on is
**structure**: what the bytes *are*. It does not decide whether this deployment can serve them — an
open framework set cannot be judged by a closed list (ADR-0027 dec 2), and the registry that knows is
discovered, lives on the serving side, and is asked at the registration gate instead (ADR-0030
dec 5/6 rev 1).

Note the precise claim: refused **before they reach the tenant's namespace**, not before they exist.
An artifact is staged first and admitted second, because the two decisions in play pull opposite
ways — the byte half of this rule needs the bytes, and ADR-0019 dec 6 keeps the mediator out of the
artifact data path so that it scales on request rate rather than artifact size. Routing the largest
artifacts the platform will ever see through its control plane to satisfy a check is the wrong trade;
reading their heads out of a staging area is not. So unadmitted bytes exist, briefly, somewhere no
tenant can address and nothing loads — and never in the namespace that would make them a model.

The rule is one question, and it is about the **serialisation, never the framework**:

    does loading this artifact require deserialising author-supplied objects?

If yes, it is refused — whatever flavor carries it. This module therefore names no framework and no
safe format. A list of known-good frameworks would be domain knowledge in a core path (ADR-0008
dec 1), would need amending for every framework that ever ships, and would refuse a safe format
nobody had thought of yet. ADR-0027 keeps its own list "an instance, not the rule" for the same
reason.

Why this refusal is the precondition rather than hardening (ADR-0030 dec 4): for a kernel-authored
artifact, an executable stream grants its author code execution in a sandbox where they already had
it. For an uploaded artifact the author has no foothold at all, so the same bytes would hand
arbitrary code execution to anyone who can authenticate a session — on the trusted side, under the
serving environment's credentials. Same format, different blast radius, because provenance changed.

**MLflow-format knowledge is not framework knowledge.** The gateway already speaks MLflow's wire
protocol; reading the artifact's own manifest is reading the format it mediates. The line this module
holds is that it never asks *which library* wrote the model.

The rule is applied twice over, because either half alone is evadable: a manifest can *declare* an
executable serialisation, and a file can *be* one while the manifest says otherwise. Neither is
trusted to speak for the other. This is defence in depth and not belt-and-braces — the serving side
additionally refuses to deserialise such a stream at all, so a miss here is still not a load.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Tuple

import yaml

# Python's object-serialisation family. These are the *mechanisms* whose load path can execute
# author-supplied code; they are not tied to any framework, which is the point.
_EXECUTABLE_SERIALISATIONS = frozenset({"pickle", "cloudpickle", "dill", "joblib"})

# A manifest key that, by its presence alone, says an object stream was used — no value to inspect.
# `cloudpickle_version` is recorded when a flavor pickles author objects; `python_model` is a
# caller-supplied object by construction, and carries no serialisation declaration to read.
_EXECUTABLE_MARKER_KEYS = frozenset({"cloudpickle_version", "python_model"})

# Pickle protocols 2-5 open with 0x80 then the protocol number — the stream framing, not a file
# extension, so a rename does not evade it. Protocols 0/1 have no such framing; they are caught by
# the manifest half instead, since a stream MLflow never declares is a stream MLflow never loads.
_PICKLE_PROTO_MAGIC = 0x80
_PICKLE_PROTO_RANGE = range(2, 6)

MANIFEST_NAME = "MLmodel"


@dataclass(frozen=True)
class Verdict:
    """Admitted, or refused with a reason an operator can act on (ADR-0027 dec 2's posture: a
    refusal with a reason is the honest answer, never a silent drop)."""

    admitted: bool
    reason: str = ""

    @property
    def refused(self) -> bool:
        return not self.admitted


def parse_manifest(text: str) -> Optional[dict]:
    """Parse an artifact manifest, or ``None`` if it is not a mapping.

    ``safe_load``, never ``load``: this module exists to refuse a format that executes code on
    deserialisation, and a parser that constructs arbitrary Python from the same untrusted upload
    would hand back exactly what the module is here to deny.
    """
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _walk(node: Any) -> Iterable[Tuple[Optional[str], Any]]:
    """Every (key, value) in a nested mapping/sequence. Generic on purpose: the manifest's shape
    differs per flavor, and this rule must not depend on knowing any of them."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield (str(key) if key is not None else None), value
            yield from _walk(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


def declared_executable_serialisation(manifest: dict) -> Optional[str]:
    """The reason this manifest declares an executable serialisation, or ``None``.

    Walks the whole manifest rather than reading a known flavor's known key, because which key
    carries the declaration is a per-flavor detail this module refuses to learn.
    """
    for key, value in _walk(manifest.get("flavors", {})):
        if key in _EXECUTABLE_MARKER_KEYS:
            return f"manifest declares '{key}', which is an author-supplied object stream"
        if isinstance(value, str) and value.strip().lower() in _EXECUTABLE_SERIALISATIONS:
            return (
                f"manifest declares '{key}: {value}', an object serialisation whose load path can "
                f"execute author-supplied code"
            )
    return None


def is_executable_object_stream(head: bytes) -> bool:
    """Whether these opening bytes frame a Python object stream, by framing rather than by name."""
    return len(head) >= 2 and head[0] == _PICKLE_PROTO_MAGIC and head[1] in _PICKLE_PROTO_RANGE


def evaluate(manifest_text: Optional[str], files: Sequence[Tuple[str, bytes]]) -> Verdict:
    """Admit or refuse an artifact on structure alone (ADR-0030 dec 4).

    ``files`` is (path, opening bytes) for every file in the artifact — the head is enough to frame a
    stream, so the gateway never needs the whole object in memory to refuse it.
    """
    if not manifest_text:
        return Verdict(False, f"artifact has no {MANIFEST_NAME} manifest, so what it is cannot be established")

    manifest = parse_manifest(manifest_text)
    if manifest is None:
        return Verdict(False, f"{MANIFEST_NAME} is not a readable mapping")
    if not isinstance(manifest.get("flavors"), dict) or not manifest["flavors"]:
        return Verdict(False, f"{MANIFEST_NAME} declares no flavors, so what it is cannot be established")

    declared = declared_executable_serialisation(manifest)
    if declared:
        return Verdict(False, declared)

    for path, head in files:
        if is_executable_object_stream(head):
            return Verdict(
                False,
                f"'{path}' is an author-supplied object stream, whatever the manifest says it is",
            )

    return Verdict(True)
