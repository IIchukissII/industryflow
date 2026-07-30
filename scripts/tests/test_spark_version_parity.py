# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the Spark version parity check.

The check is a gate, so the thing worth testing is that it FAILS when it should. The bug it exists
to catch is a real one that reached an open PR: a grouped bot bump moved
`FROM apache/spark:4.1.2-python3` to `4.1.3` in three Dockerfiles and left four
`spark-sql-kafka-0-10_2.13:4.1.2` coordinates untouched. Nothing else in CI objected — the build
resolves the older connector happily, and the image smoke asserts `version 4.1`, which 4.1.3
satisfies. The first test below is that exact diff.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_spark_version_parity import find_mismatches  # noqa: E402

# The shape of the real file, reduced to the three lines that carry a version.
DOCKERFILE = """\
FROM apache/spark:{base}-python3
USER root
RUN /opt/spark/bin/spark-submit --master "local[1]" \\
        --packages org.apache.spark:spark-sql-kafka-0-10_2.13:{warm},org.postgresql:postgresql:42.7.4 \\
        /tmp/warm.py
CMD ["/opt/spark/bin/spark-submit", \\
     "--packages", "org.apache.spark:spark-sql-kafka-0-10_2.13:{cmd},org.postgresql:postgresql:42.7.4", \\
     "/app/spark_streaming/kafka_to_timescaledb.py"]
"""


class TestTheBumpThatPromptedThis:
    def test_the_real_diff_is_caught(self):
        # Exactly what the bot proposed: base moved, both coordinates left behind.
        text = DOCKERFILE.format(base="4.1.3", warm="4.1.2", cmd="4.1.2")
        assert len(find_mismatches(text)) == 2

    def test_both_coordinates_are_reported_not_just_the_first(self):
        # Two places per file drift independently; fixing only the one the error names would leave
        # the runtime CMD still wrong and the next run still green.
        text = DOCKERFILE.format(base="4.1.3", warm="4.1.2", cmd="4.1.2")
        lines = [line for line, _, _ in find_mismatches(text)]
        assert lines == [4, 7]

    def test_the_half_fix_is_still_caught(self):
        # The build-time warm updated, the runtime CMD forgotten — the more likely hand-fix.
        text = DOCKERFILE.format(base="4.1.3", warm="4.1.3", cmd="4.1.2")
        assert len(find_mismatches(text)) == 1

    def test_agreement_passes(self):
        text = DOCKERFILE.format(base="4.1.3", warm="4.1.3", cmd="4.1.3")
        assert find_mismatches(text) == []


class TestWhatMustNotBeMistakenForAgreement:
    def test_a_patch_difference_is_a_mismatch(self):
        # The whole point. 4.1.2 and 4.1.3 are the same major AND the same minor, so any rule
        # coarser than exact equality is a no-op on the drift this check was written for.
        text = DOCKERFILE.format(base="4.1.3", warm="4.1.2", cmd="4.1.3")
        assert len(find_mismatches(text)) == 1

    def test_a_file_with_no_coordinates_is_not_a_mismatch(self):
        # The worker image resolves no packages of its own; it has a base and nothing to disagree
        # with it. Silence there is correct, not a missed check.
        assert find_mismatches("FROM apache/spark:4.1.3-python3\nUSER root\n") == []

    def test_a_missing_base_is_an_error_not_a_pass(self):
        # If the FROM line stops being recognisable, the check knows nothing about this file. It has
        # to say so — returning "no mismatches" would be a green tick over an uninspected file.
        with pytest.raises(ValueError):
            find_mismatches('CMD ["--packages", "org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.3"]')
