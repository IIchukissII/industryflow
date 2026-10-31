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

from check_spark_version_parity import check, find_mismatches  # noqa: E402

# The shape of the real file, reduced to the three lines that carry a version.
DOCKERFILE = """\
FROM apache/spark:{base}-java{java}-python3
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
        text = DOCKERFILE.format(java=21, base="4.1.3", warm="4.1.2", cmd="4.1.2")
        assert len(find_mismatches(text)) == 2

    def test_both_coordinates_are_reported_not_just_the_first(self):
        # Two places per file drift independently; fixing only the one the error names would leave
        # the runtime CMD still wrong and the next run still green.
        text = DOCKERFILE.format(java=21, base="4.1.3", warm="4.1.2", cmd="4.1.2")
        lines = [line for line, _, _ in find_mismatches(text)]
        assert lines == [4, 7]

    def test_the_half_fix_is_still_caught(self):
        # The build-time warm updated, the runtime CMD forgotten — the more likely hand-fix.
        text = DOCKERFILE.format(java=21, base="4.1.3", warm="4.1.3", cmd="4.1.2")
        assert len(find_mismatches(text)) == 1

    def test_agreement_passes(self):
        text = DOCKERFILE.format(java=21, base="4.1.3", warm="4.1.3", cmd="4.1.3")
        assert find_mismatches(text) == []


class TestWhatMustNotBeMistakenForAgreement:
    def test_a_patch_difference_is_a_mismatch(self):
        # The whole point. 4.1.2 and 4.1.3 are the same major AND the same minor, so any rule
        # coarser than exact equality is a no-op on the drift this check was written for.
        text = DOCKERFILE.format(java=21, base="4.1.3", warm="4.1.2", cmd="4.1.3")
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


class TestTheRestOfTheRepository:
    """The version is also written outside the Dockerfiles: the compose master image, connector
    coordinates in job code, the test suite's pyspark pin. Each drifted once in this repository."""

    @staticmethod
    def tree(tmp_path, *, compose="4.1.3-java21", code=None, pyspark="4.1.3", bases=("4.1.3",),
             javas=None, ci_java="21"):
        jobs = tmp_path / "services" / "spark_jobs"
        jobs.mkdir(parents=True)
        for i, base in enumerate(bases):
            java = (javas or {}).get(i, 21)
            (jobs / f"Dockerfile.job{i}").write_text(DOCKERFILE.format(java=java, base=base, warm=base, cmd=base))
        (tmp_path / "docker-compose.yml").write_text(
            f"services:\n  spark-master:\n    image: apache/spark:{compose}-python3\n")
        (tmp_path / "deploy" / "helm").mkdir(parents=True)
        (tmp_path / "deploy" / "helm" / "values.yaml").write_text("image: {}\n")
        coordinate = f'"org.apache.spark:spark-sql-kafka-0-10_2.13:{code}"' if code else '"none"'
        (jobs / "job.py").write_text(f"PACKAGES = {coordinate}\n")
        (jobs / "requirements-dev.txt").write_text(f"pyspark=={pyspark}\npytest==9.1.1\n")
        (tmp_path / "clients").mkdir()
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        java = f', java: "{ci_java}"' if ci_java else ""
        (workflows / "unit-tests.yml").write_text(
            f"          - {{ service: spark_jobs, path: services/spark_jobs/tests{java} }}\n")
        return tmp_path

    def test_a_consistent_tree_passes(self, tmp_path):
        assert check(self.tree(tmp_path)) == []

    def test_the_compose_master_left_behind_is_caught(self, tmp_path):
        # What the base bump left behind: workers on 4.1.3, the master still on 4.1.2.
        errors = check(self.tree(tmp_path, compose="4.1.2-java21"))
        assert len(errors) == 1 and "docker-compose.yml:3" in errors[0]

    def test_a_coordinate_in_job_code_is_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, code="4.1.2"))
        assert len(errors) == 1 and "job.py:1" in errors[0]

    def test_the_pyspark_pin_is_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, pyspark="4.1.2"))
        assert len(errors) == 1 and "requirements-dev.txt:1" in errors[0]

    def test_images_that_disagree_with_each_other_are_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, bases=("4.1.3", "4.1.2")))
        assert len(errors) == 1 and "disagree" in errors[0]

    def test_a_group_matching_no_file_is_an_error_not_a_pass(self, tmp_path):
        root = self.tree(tmp_path)
        (root / "docker-compose.yml").unlink()
        assert any("no compose files matched" in e for e in check(root))

    def test_the_repository_itself_is_consistent(self):
        assert check() == []


class TestTheJava:
    """The images' JDK is fixed by their base tag; the master and the runner-side tests must match.
    The tests ran on the runner's JDK while the images ran Java 21, until a runner image changed its
    default JDK and Spark stopped starting."""

    tree = staticmethod(TestTheRestOfTheRepository.tree)

    def test_a_base_naming_no_java_is_an_error(self, tmp_path):
        root = self.tree(tmp_path)
        f = root / "services" / "spark_jobs" / "Dockerfile.job0"
        f.write_text(f.read_text().replace("-java21-python3", "-python3"))
        assert any("names no Java" in e for e in check(root))

    def test_images_disagreeing_on_java_are_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, bases=("4.1.3", "4.1.3"), javas={1: 17}))
        assert any("disagree on their Java" in e for e in errors)

    def test_a_master_on_another_java_is_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, compose="4.1.3-java17"))
        assert len(errors) == 1 and "docker-compose.yml:3" in errors[0] and "Java 21" in errors[0]

    def test_a_master_naming_no_java_is_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, compose="4.1.3"))
        assert len(errors) == 1 and "docker-compose.yml:3" in errors[0]

    def test_ci_on_another_java_is_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, ci_java="17"))
        assert len(errors) == 1 and "unit-tests.yml" in errors[0] and "Java 17" in errors[0]

    def test_ci_naming_no_java_is_caught(self, tmp_path):
        errors = check(self.tree(tmp_path, ci_java=None))
        assert len(errors) == 1 and "names no `java`" in errors[0]
