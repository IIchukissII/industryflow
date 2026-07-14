# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for ADR-0027's declarative parity check.

The check is a gate, so the thing worth testing is that it FAILS when it should. A parity check that
cannot fail is worse than none: it is a green tick that says the boundary was inspected.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_model_env_parity import (  # noqa: E402
    RULES,
    SameComponents,
    ServingAtLeastKernel,
    parse_pins,
    serving_requirements_path,
)


class TestScikitLearnIsNotSemver:
    """The trap this check exists to avoid, and very nearly fell into.

    The obvious rule for scikit-learn is "the majors must match". It is a NO-OP: scikit-learn has
    been on major 1 since 2021, so the exact drift ADR-0027 was written to close — a kernel on 1.5.1
    serving against 1.9.0 — passes a major-only check. The rule has to be major.minor.
    """

    def test_the_real_drift_that_prompted_adr_0027_is_caught(self):
        assert RULES["scikit-learn"].check("1.5.1", "1.9.0") is not None

    def test_a_major_only_rule_would_have_missed_it(self):
        # Documents WHY the pin is major.minor. If someone "simplifies" the rule to majors, this is
        # the test that explains what they just switched off.
        assert SameComponents(1, "major").check("1.5.1", "1.9.0") is None

    def test_patch_drift_is_permitted(self):
        # ADR-0027 dec 3: patch drift within a minor is accepted residual risk, surfaced by the
        # model's compatibility status rather than blocked at build time.
        assert RULES["scikit-learn"].check("1.9.0", "1.9.2") is None


class TestNumpyMajor:
    def test_the_abi_break_is_caught(self):
        assert RULES["numpy"].check("1.26.4", "2.5.1") is not None

    def test_minor_drift_within_a_major_is_permitted(self):
        assert RULES["numpy"].check("2.3.3", "2.5.1") is None


class TestTheSerializersAreInTheContract:
    """skops and xgboost WRITE THE ARTIFACT'S BYTES, so they are on the path whether or not it is obvious.

    Both were nearly missed, for opposite reasons. skops looked like an unused hardening library
    sitting in the kernel image — it is in fact the format mlflow 3's sklearn flavor writes by default
    (`model.skops`, not a pickle), and mlflow bounds it only as `skops<1`, so on the serving side it
    floated. xgboost looked like an ordinary ML library — its models cross as native boosters via the
    `mlflow.xgboost` flavor.
    """

    def test_skops_is_in_the_contract(self):
        assert "skops" in RULES

    def test_a_serving_skops_older_than_the_kernels_is_caught(self):
        # The kernel would write a skops file that this serving image cannot be trusted to read.
        assert RULES["skops"].check("0.14.0", "0.11.0") is not None

    def test_serving_skops_newer_is_fine(self):
        assert RULES["skops"].check("0.11.0", "0.14.0") is None


class TestXgboostDirection:
    """xgboost is a direction, not an equality — and the direction is the whole point."""

    def test_serving_older_than_the_kernel_is_caught(self):
        # The live gap when ADR-0027 was written: the artifact crossed BACKWARDS, into an older
        # reader than wrote it.
        reason = ServingAtLeastKernel().check("2.1.1", "2.0.3")
        assert reason is not None
        assert "BACKWARDS" in reason

    def test_serving_newer_than_the_kernel_is_fine(self):
        # Loading runs forward: an old model into a new reader. That is the supported direction.
        assert ServingAtLeastKernel().check("2.0.3", "2.1.1") is None

    def test_equal_is_fine(self):
        assert ServingAtLeastKernel().check("2.1.1", "2.1.1") is None

    def test_a_major_gap_is_caught_in_either_direction(self):
        assert ServingAtLeastKernel().check("2.1.1", "3.0.0") is not None


class TestParsePins:
    def test_reads_a_requirements_file(self):
        pins = parse_pins("# a comment\nnumpy==2.5.1\nscikit-learn==1.9.0  # inline\n")
        assert pins == {"numpy": "2.5.1", "scikit-learn": "1.9.0"}

    def test_reads_a_dockerfile_pip_install_layer(self):
        # The kernel pins live in continuation lines of a RUN, not in a requirements file. One parser
        # has to read both shapes.
        pins = parse_pins(
            "RUN pip install --no-cache-dir \\\n"
            "    numpy==2.5.1 \\\n"
            "    scikit-learn==1.9.0\n"
        )
        assert pins == {"numpy": "2.5.1", "scikit-learn": "1.9.0"}

    def test_reads_a_pin_carrying_extras(self):
        assert parse_pins("redis[asyncio]==8.0.1\n") == {"redis": "8.0.1"}

    def test_ignores_unpinned_and_ranged_requirements(self):
        assert parse_pins("numpy>=2.5.1\nsomething\n") == {}


class TestTheCheckReadsWhatTheImageInstalls:
    """ADR-0027 dec 5. This is the mistake that was actually made, so it gets a test.

    `services/ml_service/requirements.txt` was installed by no image, listed numpy/sklearn/pandas
    anyway, and fooled both an architecture review and ADR-0027's first draft into concluding that
    xgboost was absent from serving. The path is derived from the Dockerfile's COPY so it cannot
    drift back to a file nobody installs.
    """

    def test_resolves_to_the_file_on_the_images_build_path(self):
        assert serving_requirements_path().parts[-3:] == ("ml_service", "api", "requirements.txt")

    def test_the_phantom_is_gone_and_stays_gone(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        phantom = repo_root / "services" / "ml_service" / "requirements.txt"
        assert not phantom.exists(), (
            "services/ml_service/requirements.txt is back. It is installed by no image (the serving "
            "image builds from api/requirements.txt) but it reads like the service's manifest, and "
            "the repo-wide resolve gate will keep it green forever while it teaches every reader "
            "something false. ADR-0027 dec 9 removed it."
        )


def test_the_real_repository_satisfies_the_contract():
    """The end-to-end assertion: the pins actually in the tree agree. This is the gate, in-process."""
    from check_model_env_parity import main

    assert main() == 0, "The repository violates ADR-0027's train/serve parity contract."


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
