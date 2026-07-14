#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The declarative half of ADR-0027's gate: do the two ends of the model artifact path agree?

ADR-0027 dec 1 is the rule — *the artifact declares its requirements; the serving environment
satisfies them or refuses the model* — and it is enforced at runtime, against a real artifact, by
ml_service. This script is not that. This is dec 5's **declarative check**: it compares what the two
environments *say* they are, before either is built, and fails the change that would break the
contract. It is the check whose absence let the gap open in the first place (ml_service took numpy 2
alone, and nothing was watching).

It is deliberately NOT the authority. It reasons about version numbers, and a version number is a
proxy for the thing we actually care about — that the pickle loads. The authority is the empirical
round-trip gate (train in the real kernel image, load and score in the real serving image). This
runs in seconds on every PR; that one builds two images.

WHERE THE VERSIONS COME FROM — this is itself a decision (ADR-0027 dec 5), not a detail. The serving
pins are read from the requirements file named in the serving image's own Dockerfile `COPY`, not from
a path we think looks right. `services/ml_service/requirements.txt` used to exist, was installed by
no image, listed numpy/sklearn/pandas anyway, and consequently fooled both an architecture review and
this ADR's first draft into believing xgboost was absent from serving. A check pointed at a file no
image installs is green and meaningless. So: the Dockerfile is the oracle.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The authoring kernel — the environment models are TRAINED in (ADR-0011 dec 5). Pins live in `pip
# install` layers rather than a requirements file, so the Dockerfile is read directly.
KERNEL_DOCKERFILE = Path("services/notebook_hub/Dockerfile.authoring")

# The serving image — the environment models are LOADED in. Its requirements file is not named here:
# it is read out of the Dockerfile's COPY (see module docstring). The build context mirrors the one
# images.yml/docker-compose build it with, because a COPY source is relative to the context.
SERVING_DOCKERFILE = Path("services/ml_service/api/Dockerfile")
SERVING_BUILD_CONTEXT = Path("services/ml_service")

# The analytics kernel is deliberately absent: it trains nothing and registers nothing, so it is not
# on the artifact path and is not bound by this contract (ADR-0027 dec 12).


class Rule:
    """How one library's versions must relate across the boundary."""

    def check(self, kernel: str, serving: str) -> str | None:
        """Return None if the pins satisfy this rule, else a human-readable reason they do not."""
        raise NotImplementedError


def _parts(version: str) -> list[int]:
    return [int(p) for p in re.findall(r"\d+", version)]


@dataclass(frozen=True)
class SameComponents(Rule):
    """The first N version components must be equal at both ends.

    N=1 (major) for numpy: 1 -> 2 is a C-level ABI break, and an array pickled under one major is not
    reliably readable under the other.

    N=2 (major.minor) for scikit-learn, and this is the one place where reaching for semver would
    quietly produce a no-op check. scikit-learn has been on major 1 since 2021, so 1.5 and 1.9 — the
    very drift ADR-0027 exists to close — are *the same major*. Its own contract is stricter than
    semver's anyway: it guarantees pickle compatibility across no boundary at all and raises
    InconsistentVersionWarning on any difference. major.minor is where its serialisation surface
    actually moves.
    """

    n: int
    label: str

    def check(self, kernel: str, serving: str) -> str | None:
        k, s = _parts(kernel)[: self.n], _parts(serving)[: self.n]
        if k != s:
            return (
                f"{self.label} must match: kernel {kernel} vs serving {serving}. "
                f"A model pickled by one cannot be trusted to load in the other."
            )
        return None


@dataclass(frozen=True)
class ServingAtLeastKernel(Rule):
    """The serving version must be >= the kernel's, within the same major.

    This is a *direction*, not an equality, and the direction is the point. Loading runs forward: an
    old model into a new reader. The reverse — a new model into an OLD reader — is the one case with
    no compatibility story, and it is exactly where xgboost sat (kernel 2.1.1, serving 2.0.3) when
    ADR-0027 was written.

    It is the right rule for the libraries that *write the artifact's bytes* — xgboost's native
    booster and skops both version their own format and read older ones forward, but neither promises
    that an older reader can make sense of a newer writer's file.
    """

    def check(self, kernel: str, serving: str) -> str | None:
        k, s = _parts(kernel), _parts(serving)
        if k[:1] != s[:1]:
            return f"major must match: kernel {kernel} vs serving {serving}"
        if s < k:
            return (
                f"serving must be >= the kernel: kernel {kernel} vs serving {serving}. "
                f"The artifact would cross BACKWARDS, into an older reader than wrote it. "
                f"Close this by moving serving UP, never by pinning the kernel down."
            )
        return None


# The instantiation of ADR-0027 dec 1 for the flavors served in-process TODAY (dec 3). This list is
# not the rule and must not be read as one — it grows when the supported flavor set grows, which is
# the rule working rather than the rule being amended. A framework the serving image does not carry
# at all (torch, keras) is NOT a gap here: it is refused at the gate by ml_service, honestly, and
# made servable by the model-adapter contract (ADR-0027 dec 4 -> ADR-0010's deferred contract).
RULES: dict[str, Rule] = {
    "numpy": SameComponents(1, "numpy major"),
    "scikit-learn": SameComponents(2, "scikit-learn major.minor"),
    # pandas frames cross the predict() call, not the artifact — no pandas object is inside the
    # serialized model. So it is bound here, at build time, and is NOT grounds for refusing a model at
    # the runtime gate (ADR-0027 dec 3).
    "pandas": SameComponents(1, "pandas major"),
    # THE TWO SERIALIZERS. Neither of these is what it looks like at a glance:
    #
    # skops — mlflow 3's sklearn flavor writes `model.skops`, NOT a pickle. So skops is not an
    #   optional hardening library sitting unused in the kernel (which is what ADR-0027's first draft
    #   assumed); it is the format the artifact is IN. mlflow only bounds it as `skops<1`, so on the
    #   serving side it floated until it was pinned — the kernel writing one version and the serving
    #   image reading whatever pip resolved that day.
    #
    # xgboost — an xgboost model goes through the `mlflow.xgboost` flavor, which writes a NATIVE
    #   BOOSTER (json/ubj), not a pickled wrapper. It also carries a hard floor that has nothing to do
    #   with parity: xgboost 2.x cannot even save under scikit-learn 1.9 (`_estimator_type` was
    #   removed in favour of `__sklearn_tags__`), so 3.x is mandatory on both ends.
    "skops": ServingAtLeastKernel(),
    "xgboost": ServingAtLeastKernel(),
}

PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([0-9][0-9A-Za-z.\-]*)")


def parse_pins(text: str) -> dict[str, str]:
    """Pull `name==version` pins out of a requirements file or a Dockerfile's pip install layers.

    Tolerates the trailing backslashes of a multi-line `pip install`, inline comments, and extras
    markers (`redis[asyncio]==8.0.1`), because both file shapes have to be read by one parser.
    """
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
        # A Dockerfile pins inside `RUN pip install --no-cache-dir \` continuation lines; strip any
        # leading shell noise so the pin itself is what gets matched.
        line = re.sub(r"^(RUN\s+|pip\s+install\s+|--[\w-]+(=\S+)?\s*)+", "", line).strip()
        name = line.split("[", 1)[0]  # redis[asyncio]==8.0.1 -> redis==8.0.1
        if "[" in line and "]" in line:
            line = name + line[line.index("]") + 1 :]
        if m := PIN.match(line):
            pins[m.group(1).lower()] = m.group(2)
    return pins


def serving_requirements_path() -> Path:
    """Resolve the serving image's requirements file FROM ITS DOCKERFILE — never by convention.

    ADR-0027 dec 5: the authoritative source is the file on the image's build path. Guessing it by
    name is how the phantom `services/ml_service/requirements.txt` got believed. If the Dockerfile's
    COPY ever moves, this check moves with it instead of silently reading a file nobody installs.
    """
    dockerfile = REPO_ROOT / SERVING_DOCKERFILE
    copies = re.findall(
        r"^COPY\s+(?:--\S+\s+)*(\S*requirements\S*\.txt)\s",
        dockerfile.read_text(),
        re.MULTILINE,
    )
    if len(copies) != 1:
        raise SystemExit(
            f"{SERVING_DOCKERFILE}: expected exactly one COPY of a requirements file, found "
            f"{len(copies)} ({copies}). This check reads what the image installs; it cannot do "
            f"that if the Dockerfile is ambiguous about which file that is."
        )
    return REPO_ROOT / SERVING_BUILD_CONTEXT / copies[0]


def main() -> int:
    kernel_file = REPO_ROOT / KERNEL_DOCKERFILE
    serving_file = serving_requirements_path()

    kernel = parse_pins(kernel_file.read_text())
    serving = parse_pins(serving_file.read_text())

    print(f"kernel  (trains): {KERNEL_DOCKERFILE}")
    print(f"serving (loads):  {serving_file.relative_to(REPO_ROOT)}  [read from the Dockerfile COPY]")
    print()

    failures = 0
    for package, rule in RULES.items():
        k, s = kernel.get(package), serving.get(package)
        if k is None or s is None:
            # Not a violation. A library only one side carries is not a broken contract — it is a
            # flavor the serving image does not support, which ml_service refuses at the gate with a
            # reason (ADR-0027 dec 1-2). Say so, and keep going.
            where = "the kernel" if k is None else "serving"
            print(f"  --  {package}: not pinned in {where}; not on the shared artifact path")
            continue

        if reason := rule.check(k, s):
            print(f"  FAIL {package}: {reason}")
            print(f"::error file={KERNEL_DOCKERFILE}::ADR-0027 train/serve parity — {package}: {reason}")
            failures += 1
        else:
            print(f"  ok   {package}: kernel {k} / serving {s}")

    print()
    if failures:
        print(
            f"{failures} package(s) violate ADR-0027's train/serve parity contract.\n"
            "A model trained in the authoring kernel would cross this boundary and be unpickled by a\n"
            "serving environment that cannot be trusted to read it — silently, and possibly scoring\n"
            "wrong rather than crashing. Move the two ends together, or record a new decision."
        )
        return 1

    print("Both ends of the model artifact path agree (ADR-0027 dec 3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
