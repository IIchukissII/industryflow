# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for ADR-0027's runtime gate: does this environment satisfy what the artifact declares?

The gate exists to REFUSE, so the tests that matter are the ones that prove it refuses — and, just as
importantly, that it refuses for a reason a human can act on, and that it does not refuse things it
should serve.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import model_compat  # noqa: E402
from model_compat import (  # noqa: E402
    COMPATIBLE,
    INCOMPATIBLE,
    PATCH_DRIFT,
    ArtifactUnreadable,
    Compatibility,
    evaluate,
)


@pytest.fixture
def artifact(monkeypatch):
    """Let a test state what the artifact declares, and what this environment happens to have."""

    def _setup(declared: dict, present: dict, flavor: str = "mlflow.sklearn", importable=True):
        monkeypatch.setattr(model_compat, "_read_declarations", lambda uri: (flavor, declared))
        # `present` is the whole truth about this environment: it answers both "is this declared
        # package here" and "is the flavor's framework here".
        monkeypatch.setattr(model_compat, "_installed", lambda pkg: present.get(pkg))
        monkeypatch.setattr(model_compat, "_flavor_importable", lambda f: importable)

    return _setup


class TestTheDriftThatPromptedTheAdr:
    def test_a_numpy_major_gap_is_refused(self, artifact):
        # The live gap: kernel trained on numpy 1, serving runs numpy 2. It does not reliably raise on
        # load — it can score silently wrong, which is the whole reason this gate exists.
        artifact({"numpy": "1.26.4", "scikit-learn": "1.9.0"},
                 {"numpy": "2.5.1", "scikit-learn": "1.9.0"})
        result = evaluate("runs:/abc/model")
        assert result.status == INCOMPATIBLE
        assert not result.servable
        assert any("numpy" in r for r in result.reasons)

    def test_a_scikit_learn_minor_gap_is_refused(self, artifact):
        # 1.5 vs 1.9 — the exact drift ADR-0027 closes. Both are semver-major 1, so a major-only rule
        # would call this fine. It is not fine.
        artifact({"scikit-learn": "1.5.1"}, {"scikit-learn": "1.9.0"})
        assert evaluate("runs:/abc/model").status == INCOMPATIBLE

    def test_matching_versions_are_compatible(self, artifact):
        artifact({"numpy": "2.5.1", "scikit-learn": "1.9.0", "skops": "0.14.0"},
                 {"numpy": "2.5.1", "scikit-learn": "1.9.0", "skops": "0.14.0"})
        result = evaluate("runs:/abc/model")
        assert result.status == COMPATIBLE
        assert result.servable


class TestTheOpenFrameworkSet:
    """The reason ADR-0027's rule is generic instead of a list of libraries.

    A torch model is not a special case in the code — it is refused by the same rule that catches a
    numpy drift, on the day it arrives, without anyone having amended anything.
    """

    def test_a_torch_model_is_refused_honestly_not_served_badly(self, artifact):
        artifact(
            {"torch": "2.9.0", "numpy": "2.5.1"},
            {"numpy": "2.5.1"},  # no torch here
            flavor="mlflow.pytorch",
            importable=False,
        )
        result = evaluate("runs:/abc/model")
        assert result.status == INCOMPATIBLE
        # The refusal must NAME the thing and point at the remedy, or it is just a wall.
        assert any("mlflow.pytorch" in r for r in result.reasons)
        assert any("torch" in r for r in result.reasons)
        assert any("adapter" in r.lower() for r in result.reasons)

    def test_a_missing_framework_is_refused_even_when_the_flavor_module_imports(self, artifact):
        # mlflow imports its frameworks LAZILY, so `import mlflow.pytorch` can succeed in an image with
        # no torch at all. Anchor the refusal on the framework being installed, not on that import.
        artifact({"torch": "2.9.0"}, {}, flavor="mlflow.pytorch", importable=True)
        result = evaluate("runs:/abc/model")
        assert result.status == INCOMPATIBLE
        assert result.flavor_supported is False


class TestItDoesNotRefUseModelsThatWork:
    """The false refusal the BOX caught — a gate that rejects a servable model is worse than no gate.

    MLflow's requirements.txt describes the TRAINING environment, not what is needed to LOAD. The first
    cut treated any declared-but-absent package as fatal, and refused a model the kernel had just
    trained because the artifact named `psutil` (a transitive dep of the kernel's mlflow client). The
    model then loaded and scored with a delta of exactly 0.0 — it was servable all along.
    """

    def test_a_training_only_dependency_does_not_refuse_the_model(self, artifact):
        artifact(
            {"psutil": "7.2.2", "scikit-learn": "1.9.0", "numpy": "2.5.1"},
            {"scikit-learn": "1.9.0", "numpy": "2.5.1"},   # no psutil here, and it does not matter
            flavor="mlflow.sklearn",
        )
        result = evaluate("runs:/abc/model")
        assert result.servable, f"refused a servable model over {result.faults}"
        assert "psutil" not in result.faults

    def test_but_a_missing_SERIALIZER_still_refuses(self, artifact):
        # skops writes the artifact's bytes. Its absence is not a training-env detail.
        artifact({"skops": "0.14.0", "scikit-learn": "1.9.0"},
                 {"scikit-learn": "1.9.0"}, flavor="mlflow.sklearn")
        assert evaluate("runs:/abc/model").status == INCOMPATIBLE


class TestWhatIsNotGroundsForRefusal:
    def test_pandas_absence_does_not_refuse_a_model(self, artifact):
        # ADR-0027 dec 3: pandas frames cross the predict() call, not the artifact. It is bound at
        # BUILD time by the parity check and is explicitly not a runtime refusal.
        artifact({"pandas": "2.3.3", "numpy": "2.5.1", "scikit-learn": "1.9.0"},
                 {"numpy": "2.5.1", "scikit-learn": "1.9.0"})   # no pandas here
        assert evaluate("runs:/abc/model").servable

    def test_an_unconstrained_library_version_difference_is_not_refused(self, artifact):
        # scipy is not on the deserialisation path. Recorded, not judged.
        artifact({"scipy": "1.18.0", "numpy": "2.5.1", "scikit-learn": "1.9.0"},
                 {"scipy": "1.11.0", "numpy": "2.5.1", "scikit-learn": "1.9.0"})
        result = evaluate("runs:/abc/model")
        assert result.status == COMPATIBLE
        assert result.present["scipy"] == "1.11.0"


class TestPatchDrift:
    def test_a_patch_difference_is_served_but_surfaced(self, artifact):
        # The contract holds; the versions are not identical. sklearn WILL warn on load, and ADR-0027
        # dec 7 shows that rather than swallowing it.
        artifact({"scikit-learn": "1.9.0"}, {"scikit-learn": "1.9.2"})
        result = evaluate("runs:/abc/model")
        assert result.status == PATCH_DRIFT
        assert result.servable
        assert result.reasons


class TestTheSerializers:
    def test_an_older_serving_serializer_is_refused(self, artifact):
        # skops writes the sklearn artifact's bytes. A newer writer's file meeting an older reader is
        # the backwards direction, and nothing promises it works.
        artifact({"skops": "0.14.0"}, {"skops": "0.11.0"})
        result = evaluate("runs:/abc/model")
        assert result.status == INCOMPATIBLE
        assert any("backwards" in r for r in result.reasons)

    def test_a_newer_serving_serializer_is_fine(self, artifact):
        artifact({"skops": "0.11.0", "scikit-learn": "1.9.0"},
                 {"skops": "0.14.0", "scikit-learn": "1.9.0"})
        assert evaluate("runs:/abc/model").servable

    def test_an_older_serving_xgboost_is_refused(self, artifact):
        artifact({"xgboost": "3.3.0"}, {"xgboost": "2.0.3"})
        assert evaluate("runs:/abc/model").status == INCOMPATIBLE


class TestUnreadableIsNotAVerdict:
    def test_an_unreadable_artifact_raises_rather_than_guessing(self, monkeypatch):
        # A tracking store that is down must never be reported to a data scientist as "your model is
        # broken". No claim can be made, so none is made — the endpoint turns this into a 503.
        def boom(uri):
            raise ArtifactUnreadable("tracking store unreachable")

        monkeypatch.setattr(model_compat, "_read_declarations", boom)
        with pytest.raises(ArtifactUnreadable):
            evaluate("runs:/abc/model")


class TestFaultsAreStructuredNotProse:
    """The UI reconciles two manifests library-by-library, so the fault must be KEYED BY LIBRARY.

    If this collapsed to a list of English sentences, the frontend would have to parse them back into
    a table — so the structure is produced here, once, where the facts are.
    """

    def test_a_fault_is_keyed_by_the_library_that_caused_it(self, artifact):
        artifact({"numpy": "1.26.4", "scikit-learn": "1.9.0"},
                 {"numpy": "2.5.1", "scikit-learn": "1.9.0"})
        result = evaluate("runs:/abc/model")
        assert set(result.faults) == {"numpy"}          # only the offender
        assert "major" in result.faults["numpy"]
        assert result.declared["numpy"] == "1.26.4"     # both sides, for the ledger
        assert result.present["numpy"] == "2.5.1"

    def test_a_missing_library_is_a_fault_on_that_library(self, artifact):
        artifact({"torch": "2.9.0"}, {}, flavor="mlflow.pytorch", importable=False)
        result = evaluate("runs:/abc/model")
        assert "torch" in result.faults
        assert result.present["torch"] is None          # the gap in the column IS the finding
        assert result.flavor_supported is False


class TestTheDetailIsActionable:
    def test_detail_carries_the_reasons_and_both_version_sets(self):
        c = Compatibility(
            status=INCOMPATIBLE,
            reasons=["`numpy`: major differs"],
            declared={"numpy": "1.26.4"},
            present={"numpy": "2.5.1"},
            flavor="mlflow.sklearn",
        )
        detail = c.as_detail()
        # "incompatible" on its own tells a data scientist nothing about which library to move, or
        # which way. The stored detail is what makes the refusal fixable.
        assert detail["reasons"] == ["`numpy`: major differs"]
        assert detail["declared"]["numpy"] == "1.26.4"
        assert detail["present"]["numpy"] == "2.5.1"
        assert detail["flavor"] == "mlflow.sklearn"
