# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The jars the Spark images fetch are pinned by a SHA-256 the repository holds (ADR-0026 dec 12).

The script refuses a jar without a pin when it builds, but only on the build that fetches it. This
guard fails the change itself: a version bumped in the script without its digests, a digest left for
a version nothing fetches any more, and an image that runs the script without its pins file.
"""

from __future__ import annotations

import re

from _repo import ROOT

JOBS = ROOT / "services" / "spark_jobs"
SCRIPT = (JOBS / "patch-spark-jars.sh").read_text()
PIN_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^\s/]+\.jar)$")


def _var(name: str) -> str:
    return re.search(rf"^{name}=(\S+)$", SCRIPT, re.M).group(1)


def _pins() -> dict[str, str]:
    pins = {}
    for line in (JOBS / "spark-jars.sha256").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        m = PIN_LINE.match(line)
        assert m, f"not a sha256sum line: {line!r}"
        assert m["name"] not in pins, f"{m['name']} pinned twice"
        pins[m["name"]] = m["digest"]
    return pins


def test_the_fetched_versions_and_only_those_are_pinned():
    pins = _pins()
    netty = _var("NETTY_TO")
    expected = {f"hadoop-aws-{_var('HADOOP_AWS_VERSION')}.jar", f"bundle-{_var('AWS_SDK_VERSION')}.jar"}
    assert expected <= set(pins), f"no pin for {sorted(expected - set(pins))}"
    netty_pins = {n for n in pins if n.startswith("netty-")}
    assert netty_pins, "no netty pin: the guard is not reading the pins"
    stale = sorted(n for n in set(pins) - expected if not (n.startswith("netty-") and f"-{netty}" in n))
    assert not stale, f"pins for versions the script does not fetch: {stale}"


def test_every_image_running_the_script_copies_its_pins():
    images = [p for p in JOBS.glob("Dockerfile.*") if "patch-spark-jars.sh" in p.read_text()]
    assert len(images) >= 3, "the Spark images were not found"
    for image in images:
        copy = re.search(r"^COPY\s+(.*patch-spark-jars\.sh.*)$", image.read_text(), re.M).group(1)
        assert "spark-jars.sha256" in copy.split(), f"{image.name} runs the script without its pins"
