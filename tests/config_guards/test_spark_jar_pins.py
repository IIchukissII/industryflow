# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A build input fetched by address is pinned by a SHA-256 the repository holds, and has a second
source (ADR-0026 dec 12).

The script refuses a jar without a pin when it builds, but only on the build that fetches it. This
guard fails the change itself: a version bumped in the script without its digests, a digest left for
a version nothing fetches any more, a source list cut to one, an image that runs the script without
its pins file, and an image that fetches by address outside the script. Which Netty jars are fetched
is the base image's set, known only at build time; a jar of it without a pin fails that build.
"""

from __future__ import annotations

import re
import shlex

from _repo import ROOT

JOBS = ROOT / "services" / "spark_jobs"
SCRIPT_NAME = "patch-spark-jars.sh"
SCRIPT = (JOBS / SCRIPT_NAME).read_text()
PIN_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^\s/]+\.jar)$")
# A fetch by address in a Dockerfile: curl, wget or ADD of a remote URL, not a loopback probe.
REMOTE_FETCH = re.compile(r"(?:\bcurl\b|\bwget\b|^ADD\b)[^\n]*\bhttps?://(?!127\.0\.0\.1|localhost)", re.M)


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
    assert any(n.startswith("netty-") for n in pins), "no netty pin: the guard is not reading the pins"
    stale = sorted(n for n in set(pins) - expected if not (n.startswith("netty-") and f"-{netty}" in n))
    assert not stale, f"pins for versions the script does not fetch: {stale}"


def test_the_script_has_a_second_source():
    default = re.search(r'^SOURCES=\$\{SPARK_JAR_SOURCES:-"([^"]*)"\}$', SCRIPT, re.M)
    assert default, "SOURCES default not found: the guard is not reading the script"
    sources = default.group(1).split()
    assert len(sources) >= 2, f"one source only: {sources}"
    assert all(s.startswith("https://") for s in sources), sources


def _copies(dockerfile: str) -> list[list[str]]:
    joined = re.sub(r"\\\n", " ", dockerfile)
    return [shlex.split(m) for m in re.findall(r"^COPY\s+(.*)$", joined, re.M)]


def test_every_image_running_the_script_copies_its_pins():
    images = [p for p in JOBS.glob("Dockerfile.*") if any(SCRIPT_NAME in c for c in _copies(p.read_text()))]
    assert len(images) >= 2, "the Spark images were not found"
    for image in images:
        copy = next(c for c in _copies(image.read_text()) if SCRIPT_NAME in c)
        assert "spark-jars.sha256" in copy, f"{image.name} runs the script without its pins"


def test_no_image_fetches_by_address_outside_the_script():
    dockerfiles = [p for p in ROOT.rglob("Dockerfile*") if "node_modules" not in p.parts]
    assert len(dockerfiles) >= 10, "the Dockerfiles were not found"
    fetching = [str(p.relative_to(ROOT)) for p in dockerfiles if REMOTE_FETCH.search(p.read_text())]
    assert not fetching, f"fetch by address outside {SCRIPT_NAME}, unpinned: {fetching}"
