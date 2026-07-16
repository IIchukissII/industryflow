# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the registration gate must ask of an **externally-authored** model, and no one else (ADR-0030
dec 5, 6, 8) — the pure rule, no I/O.

A kernel-authored model arrives having been *watched* being made: MLflow observed a real training
environment and recorded its requirements, and the kernel emitted the model's output semantics
because it had a platform to emit them from. An uploaded artifact has neither. Everything those two
facts used to supply is now an **assertion by a stranger**, and this module is where the difference
stops being a philosophical point and becomes a set of refusals.

Three questions, in the order they can be answered:

  1. **Does it say what its output means, and does that mean anything here?** ADR-0028 dec 2 forbids
     inferring semantics from a prediction's value or dtype, so there is no fallback: an absent or
     unknown declaration is a refusal, not a default. And "known" is not enough — the platform must
     hold something that could *act* on the declaration for this artifact's flavor, which only the
     live detector registry can answer.
  2. **Does it say what it needs?** ADR-0027 relies on MLflow's inference of a training environment
     it watched. Nothing watched this one, so the requirements are whatever someone wrote. The
     declarative check still runs against them — that half compares against what the image actually
     installs, which no artifact can lie about — but this module records that its *input* is an
     assertion rather than a record (dec 6).
  3. **Where did it come from?** Recorded as a fact, never disguised (dec 8).

What this module does NOT do is decide whether the bytes are safe to load; that is refused earlier,
on structure, at the only place that can refuse before an artifact exists as a model. And it does not
enumerate frameworks: the flavor question is asked of the registry, which is discovered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

# Provenance (ADR-0030 dec 8). A fact about the model, recorded because it is true — not a
# decoration. The platform never fabricates a run to make an uploaded artifact fit the shape the
# registry already knows: a synthetic run would make an environment nobody observed look observed,
# which is the same category of lie ADR-0028 exists to stop.
PROVENANCE_KERNEL = "kernel"      # authored in the platform's own environment, logged through it
PROVENANCE_UPLOADED = "uploaded"  # authored somewhere the platform never saw

PROVENANCE = (PROVENANCE_KERNEL, PROVENANCE_UPLOADED)


@dataclass(frozen=True)
class SemanticsVerdict:
    """Admitted, or refused with a reason an operator can act on (ADR-0027 dec 2's posture)."""

    admitted: bool
    reason: str = ""
    detector: Optional[str] = None

    @property
    def refused(self) -> bool:
        return not self.admitted


def provenance_of(*, mlflow_run_id: Optional[str], artifact_uri: Optional[str]) -> str:
    """What this model's provenance *is* — read off how it arrived, not asked of the caller.

    A caller-supplied provenance would be the one fact most worth lying about, and the platform can
    see the answer for itself: a model the platform watched being made has the run it was made in; an
    uploaded one has an artifact and no run, because dec 8 forbids inventing one for it.
    """
    return PROVENANCE_KERNEL if mlflow_run_id else PROVENANCE_UPLOADED


def judge_semantics(
    declared: Optional[str],
    flavor: Optional[str],
    *,
    vocabulary: Sequence[str],
    detectors_for: Callable[[str, str], List[str]],
) -> SemanticsVerdict:
    """Whether an uploaded artifact's declared output semantics can be honoured here.

    ``vocabulary`` and ``detectors_for`` are injected rather than imported so this rule stays pure —
    and, more to the point, so the answers come from the registry that *is* the authority rather than
    from a copy kept here. A second list would be wrong the first time an operator installed an
    adapter.
    """
    if not declared:
        # ADR-0028 dec 2: the platform never guesses. An absent declaration is not "assume the
        # common case" — #236 is what a guess costs, and it cost months.
        return SemanticsVerdict(
            False,
            "this model does not declare what its output means, and the platform will not guess "
            "(ADR-0028). Declare its score semantics and register it again.",
        )

    if declared not in vocabulary:
        return SemanticsVerdict(
            False,
            f"'{declared}' is not a score semantics this platform knows. Known: "
            f"{', '.join(sorted(vocabulary))}. A new semantics is a new registration, not a spelling "
            f"this gate should accept.",
        )

    if not flavor:
        # The artifact's own manifest names the code that would load it. Without it there is nothing
        # to match a detector against, and matching on the semantics alone would admit a model
        # nothing here can read.
        return SemanticsVerdict(
            False,
            "this artifact does not declare a flavor, so nothing can establish whether this "
            "environment could read its output.",
        )

    matches = detectors_for(declared, flavor)
    if not matches:
        return SemanticsVerdict(
            False,
            f"nothing in this deployment can score a `{flavor}` model whose output is "
            f"'{declared}'. A model whose output means nothing here is refused rather than served — "
            f"scoring it would mean guessing what its numbers say.",
        )

    return SemanticsVerdict(True, detector=sorted(matches)[0])
