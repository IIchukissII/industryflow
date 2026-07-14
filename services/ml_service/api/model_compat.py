# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-0027: the artifact declares its requirements; this environment satisfies them or refuses it.

MLflow writes the training environment INTO the run — `requirements.txt` (pinned) and `python_env.yaml`
— and then enforces none of it at load time: `mlflow.pyfunc.load_model` will happily reconstruct a model
inside an environment that cannot honour it. That is not a hypothetical. A scikit-learn model whose numpy
major has moved underneath it does not reliably raise; it can load and score WRONG, and ADR-0021 raises
alerts on those numbers. A silent wrong answer is worse than a crash.

So this module reads what the artifact declares and compares it against what is actually installed here,
and the gates (registration, deployment) refuse a model this environment cannot honestly serve.

WHY THIS IS GENERIC, AND WHY THAT MATTERS. The rule is not a list of libraries — it is "does the serving
environment satisfy what the artifact asks for". A torch model arriving at an image with no torch is
refused by exactly the same code that catches a numpy drift, with a reason, on the day it arrives — no
amendment, no new branch, nothing to remember. That is the whole point of ADR-0027 dec 1: the framework
set users may author in is OPEN, while what this image can honour is closed and declared. Making a model
we cannot serve *fail honestly* is a different (and much cheaper) thing than making it servable — that
second thing is the model-adapter contract, ADR-0010's, which ADR-0027 dec 4 hands it to.

Two version rules apply on top of "is it even installed", because the libraries that build the artifact's
bytes have compatibility stories of their own (ADR-0027 dec 3). They are stated in each library's own
terms, NOT in semver's — see SERIALIZATION_CRITICAL.

THE LIMIT OF THIS CHECK, STATED PLAINLY: it is only as complete as the artifact's declaration, and
MLflow's declaration is INFERRED, not exhaustive. Observed: a scikit-learn model logged from the kernel
declares `mlflow, pandas, scikit-learn, skops` — and **not numpy**, which is the single largest ABI risk
on this path. So this gate cannot be the only defence, and ADR-0027 never asks it to be: the build-time
parity check reads the two images' pins directly (numpy included, whatever any artifact says), and the
CI round-trip actually moves a model between the images. Three checks, none sufficient alone, and this
one is the only one that can refuse a *specific artifact* at a gate. Do not "simplify" by deleting the
other two.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import re
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as installed_version
from typing import Optional

logger = logging.getLogger(__name__)

# The three verdicts (ADR-0027 dec 7). `None` — the absence of a verdict — is a fourth, meaningful state
# held in the database: "never evaluated", which is every model registered before ADR-0027 landed.
COMPATIBLE = "compatible"
PATCH_DRIFT = "patch_drift"
INCOMPATIBLE = "incompatible"


class ArtifactUnreadable(Exception):
    """The artifact's declarations could not be read — so no claim can be made either way.

    Deliberately distinct from "incompatible". A tracking store that is down is a TRANSIENT condition and
    must not be reported to a data scientist as "your model is broken" — the gates turn this into a 503,
    not a 422. Refusing to guess is the point: this module's entire job is to stop asserting things about
    an artifact that nobody checked.
    """


def _components(v: str) -> list[int]:
    return [int(p) for p in re.findall(r"\d+", v)]


def _same_components(n: int):
    def rule(declared: str, present: str) -> Optional[str]:
        if _components(declared)[:n] != _components(present)[:n]:
            unit = "major" if n == 1 else "major.minor"
            return f"{unit} differs (model was trained against {declared}, this environment has {present})"
        return None

    return rule


def _serving_at_least(declared: str, present: str) -> Optional[str]:
    """The reader must be at least as new as the writer, within the major.

    A format library reads its own older output forward; none of them promise that an OLDER reader can
    make sense of a NEWER writer's file. So a model trained with a newer serializer than this image
    carries is refused — the artifact would be crossing backwards.
    """
    d, p = _components(declared), _components(present)
    if d[:1] != p[:1]:
        return f"major differs (model was trained against {declared}, this environment has {present})"
    if p < d:
        return (
            f"this environment is OLDER than the one that wrote the model "
            f"({present} < {declared}) — the artifact would cross backwards"
        )
    return None


# The instantiation of ADR-0027 dec 3, at runtime. Mirrors scripts/check_model_env_parity.py, which
# enforces the same contract at BUILD time against the two images' pins — that check is the fast one and
# this is the one that meets a real artifact. They are deliberately separate: a build-time check cannot
# refuse a registration, and a runtime check cannot fail a pull request.
#
# scikit-learn is major.MINOR and this is not fussiness: sklearn has been on major 1 since 2021, so a
# semver-major rule would treat 1.5 and 1.9 as equal — and 1.5-vs-1.9 is the exact drift ADR-0027 exists
# to close. It would be a check that cannot fail.
SERIALIZATION_CRITICAL = {
    "numpy": _same_components(1),
    "scikit-learn": _same_components(2),
    # These two WRITE THE ARTIFACT'S BYTES (mlflow 3 stores sklearn models as `model.skops`, and xgboost
    # models as a native booster) — neither is the incidental dependency it looks like.
    "skops": _serving_at_least,
    "xgboost": _serving_at_least,
}

# WHAT A MISSING PACKAGE ACTUALLY PROVES — and the first cut of this got it backwards.
#
# MLflow's `requirements.txt` describes the TRAINING ENVIRONMENT, not the set of things needed to LOAD
# the model. Treating "declared but absent here" as fatal therefore refuses models that serve perfectly
# well: on the box, a model the kernel had just trained was refused because the artifact declared
# `psutil` (a transitive dep of the kernel's mlflow client) — and it then loaded and scored with a delta
# of exactly 0.0. A gate that refuses what the service can demonstrably serve is worse than no gate; it
# would push data scientists to route around it.
#
# So absence is fatal for exactly two things, both of which the load genuinely cannot do without:
#
#   1. THE FLAVOR'S OWN FRAMEWORK. A `mlflow.pytorch` model needs torch to exist. This is the torch
#      refusal, and it is anchored on the flavor rather than on whether `import mlflow.pytorch` happens
#      to succeed — mlflow imports its framework lazily, so the import alone is not a reliable probe.
#   2. THE SERIALIZATION-CRITICAL SET below (numpy, scikit-learn, skops, xgboost) — the libraries that
#      wrote the artifact's bytes.
#
# Everything else the artifact names is recorded and reported, never judged.
FLAVOR_LIBRARY = {
    "mlflow.sklearn": "scikit-learn",
    "mlflow.xgboost": "xgboost",
    "mlflow.pytorch": "torch",
    "mlflow.keras": "keras",
    "mlflow.tensorflow": "tensorflow",
    "mlflow.lightgbm": "lightgbm",
}

_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)\s*==\s*([0-9][0-9A-Za-z.\-]*)")


@dataclass(frozen=True)
class Compatibility:
    """The verdict, and enough of the reasoning to act on it."""

    status: str
    reasons: list[str] = field(default_factory=list)
    declared: dict[str, str] = field(default_factory=dict)
    present: dict[str, Optional[str]] = field(default_factory=dict)
    # The fault PER LIBRARY, not just a list of sentences. What this comparison actually is, is a
    # reconciliation of two manifests — "trained against X, serving with Y" — and the UI renders it as
    # exactly that. Flattening it to prose up here would throw away the structure and force the
    # frontend to parse English back into a table.
    faults: dict[str, str] = field(default_factory=dict)
    flavor: Optional[str] = None
    flavor_supported: bool = True

    @property
    def servable(self) -> bool:
        return self.status != INCOMPATIBLE

    def as_detail(self) -> dict:
        """The JSONB payload stored beside the verdict.

        A bare "incompatible" is unactionable — it tells a data scientist nothing about which library to
        move, or in which direction. The two version sets and the per-library faults are the difference
        between a refusal they can fix and one they can only complain about.
        """
        return {
            "flavor": self.flavor,
            "flavor_supported": self.flavor_supported,
            "reasons": self.reasons,
            "declared": self.declared,
            "present": self.present,
            "faults": self.faults,
        }


def _flavor_importable(flavor: str) -> bool:
    try:
        importlib.import_module(flavor)
        return True
    except Exception:  # noqa: BLE001 — any failure to import IS the finding
        return False


def _installed(package: str) -> Optional[str]:
    try:
        return installed_version(package)
    except PackageNotFoundError:
        return None


def _read_declarations(model_uri: str) -> tuple[Optional[str], dict[str, str]]:
    """Read the flavor and the pinned requirements from the artifact — WITHOUT loading the model.

    Loading the model is the very thing we are deciding whether to permit, so the check cannot begin by
    doing it. Both of these read metadata only.

    mlflow is imported HERE rather than at module scope on purpose: the rules above — what counts as
    compatible, what gets refused — are pure, and keeping them importable without a 200MB dependency is
    what lets them be unit-tested as rules. This function is the only part that needs the tracking store.
    """
    import mlflow  # noqa: PLC0415

    try:
        info = mlflow.models.get_model_info(model_uri)
        flavor = (info.flavors or {}).get("python_function", {}).get("loader_module")
        requirements_file = mlflow.pyfunc.get_model_dependencies(model_uri, format="pip")
    except Exception as exc:  # noqa: BLE001 — the tracking store is outside our control
        raise ArtifactUnreadable(str(exc)) from exc

    declared: dict[str, str] = {}
    with open(requirements_file) as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if m := _PIN.match(line):
                declared[m.group(1).lower()] = m.group(2)

    return flavor, declared


def evaluate(model_uri: str) -> Compatibility:
    """Compare what the artifact declares against what this environment has.

    Raises ArtifactUnreadable when the declarations cannot be fetched — the caller must NOT read that as
    a verdict.
    """
    flavor, declared = _read_declarations(model_uri)

    reasons: list[str] = []
    faults: dict[str, str] = {}
    present: dict[str, Optional[str]] = {}
    status = COMPATIBLE
    flavor_supported = True

    # 1. The flavor. `loader_module` names the code that will reconstruct this model (mlflow.sklearn,
    #    mlflow.xgboost, mlflow.pytorch, ...). If we cannot even import it, this image does not carry that
    #    flavor and no version arithmetic below is worth doing. THIS IS THE TORCH CASE, and it fails here
    #    with a sentence an operator can act on rather than an ImportError three frames into a prediction.
    flavor_library = FLAVOR_LIBRARY.get(flavor or "")
    if flavor:
        # THE TORCH CASE. Anchored on the flavor's framework actually being installed, not on whether
        # `import mlflow.pytorch` succeeds — mlflow imports its frameworks lazily, so that import can
        # succeed in an image with no torch at all and would quietly wave the model through.
        missing_framework = flavor_library is not None and _installed(flavor_library) is None
        if missing_framework or not _flavor_importable(flavor):
            reasons.append(
                f"this environment cannot serve `{flavor}` models"
                + (f" — `{flavor_library}` is not installed here." if missing_framework else " — the flavor is not installed.")
                + " Serving a new model framework needs a model adapter (ADR-0027 dec 4); it is not "
                  "something a version bump can fix."
            )
            flavor_supported = False
            status = INCOMPATIBLE

    # 2. What the artifact asks for, versus what is here.
    for package, declared_version in sorted(declared.items()):
        have = _installed(package)
        present[package] = have

        if have is None:
            # Absent — but that is only a refusal if the load genuinely needs it (see FLAVOR_LIBRARY).
            # The artifact's requirements are the TRAINING environment; most of what is named there is
            # irrelevant to reconstructing the model, and refusing on it rejects models that work.
            needed = package == flavor_library or package in SERIALIZATION_CRITICAL
            if not needed:
                continue
            fault = "not installed in this environment"
            faults[package] = fault
            reasons.append(
                f"`{package}` is required to load this model ({declared_version}) and is {fault}"
            )
            status = INCOMPATIBLE
            continue

        rule = SERIALIZATION_CRITICAL.get(package)
        if rule is None:
            continue  # Out of the contract (ADR-0027 dec 3): recorded above, not judged.

        if reason := rule(declared_version, have):
            faults[package] = reason
            reasons.append(f"`{package}`: {reason}")
            status = INCOMPATIBLE
        elif have != declared_version and status == COMPATIBLE:
            # The contract holds, but not exactly. scikit-learn WILL warn on load in this case
            # (InconsistentVersionWarning); ADR-0027 dec 7 shows that rather than swallowing it.
            status = PATCH_DRIFT
            reasons.append(
                f"`{package}`: trained against {declared_version}, serving with {have} — "
                f"within the contract, but not identical"
            )

    return Compatibility(
        status=status,
        reasons=reasons,
        declared=declared,
        present=present,
        faults=faults,
        flavor=flavor,
        flavor_supported=flavor_supported,
    )


async def evaluate_run(mlflow_run_id: str) -> Compatibility:
    """`evaluate`, off the event loop — it does artifact IO against the tracking store.

    The URI is resolved rather than assumed (#240): MLflow 3 keeps a logged model outside its run, so
    a `runs:/` URI addresses nothing on a real deployment. The gate must read the same artifact the
    serving path will actually load, or it would be inspecting one thing and serving another.
    """
    from model_uri import resolve_model_uri

    return await asyncio.to_thread(evaluate, resolve_model_uri(mlflow_run_id))
