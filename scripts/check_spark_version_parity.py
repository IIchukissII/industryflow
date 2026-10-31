#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Spark version is written in many places; only some of them are bot-visible.

A Spark job image pins its Spark version in the base image tag, and then pins it AGAIN in every
`--packages` coordinate that resolves the Kafka connector — once in the build-time ivy warm, once in
the runtime CMD. The base tag is the only one a dependency bot can see: the others live inside RUN
and CMD strings, which are opaque text to it. So a routine patch bump moves the base and leaves the
coordinates behind, and nothing downstream objects:

  - the BUILD succeeds, because the older connector is still published and still resolves;
  - the SMOKE passes, because it asserts the image is Spark 4.1, which a 4.1.x base satisfies;
  - the image ships a Spark running a connector built for a different Spark.

That is the whole failure mode, and it is silent in both directions — which is why it is checked
here, statically, rather than left to a build that has no reason to complain.

The same version is written outside the Dockerfiles too, and drifts the same way: the compose file
runs the stock `apache/spark` image as the cluster master, the test suite pins `pyspark`, and job
code has carried its own connector coordinate. Every one of them must be the Dockerfiles' version.
A master on one patch and workers on another is a cluster the images never tested.

This is a declarative check, in the sense of `check_model_env_parity.py`: it compares what the files
SAY, before anything is built. It is not the authority on whether the connector works — the image
smoke and the box run are. It is the authority on whether the repository contradicts itself.

The JDK is the second fact the images fix, and it drifts the same way. A base tag without `-javaNN`
takes whatever JDK the image's default tag points at; the compose master and the CI job that runs the
Spark tests on the runner each carry their own. Each must name the images' Java.

Every file set is found by GLOB, deliberately. Naming files would make this script one more list
whose omission fails silently: a new `Dockerfile.<job>`, compose file or job module added next to the
others would simply not be checked, and the guard would stay green while the thing it guards drifted.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SPARK_DOCKERFILE_GLOB = "services/spark_jobs/Dockerfile.*"

# Everywhere else the Spark version may be written. Each group must match at least one file.
OTHER_GLOBS = {
    "compose files": ["docker-compose*.yml"],
    "deployment manifests": ["deploy/**/*.yaml"],
    "service and client code": ["services/**/*.py", "clients/**/*.py"],
    "requirements": ["services/**/requirements*.txt"],
}

# `FROM apache/spark:4.1.3-java21-python3` -> 4.1.3
BASE_RE = re.compile(r"^FROM\s+apache/spark:(?P<version>[^-\s]+)-", re.MULTILINE)

# The JDK an `apache/spark:` reference names: `4.1.3-java21-python3` -> 21; None when it names none.
IMAGE_REF_RE = re.compile(r"apache/spark:(?P<tag>\d[^\s\"'@]*)")
JAVA_RE = re.compile(r"-java(?P<java>\d+)(?:-|$)")

# The workflow that runs the Spark tests on the runner's own JDK, and its matrix entry's `java`.
CI_WORKFLOW = ".github/workflows/unit-tests.yml"
CI_JAVA_RE = re.compile(r"service:\s*spark_jobs\b[^\n}]*\bjava:\s*\"?(?P<java>\d+)")

# `org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.2` -> (2.13, 4.1.2). The trailing delimiter is
# whatever the surrounding string uses — a comma before the next coordinate, a quote, or whitespace.
COORD_RE = re.compile(
    r"org\.apache\.spark:spark-sql-kafka-0-10_(?P<scala>[\d.]+):(?P<version>[^,\"'\s\\]+)"
)

# Spark versions written outside a Dockerfile: an `apache/spark:<v>` image reference, a connector
# coordinate, a `pyspark==<v>` pin.
OTHER_PIN_RES = {
    "apache/spark image": re.compile(r"apache/spark:(?P<version>\d[^-\s\"']*)"),
    "Kafka connector coordinate": COORD_RE,
    "pyspark pin": re.compile(r"^\s*pyspark==(?P<version>[^\s;#]+)", re.MULTILINE),
}


def find_mismatches(text: str) -> list[tuple[int, str, str]]:
    """Return (line number, base version, coordinate version) for each disagreeing coordinate.

    A file with no coordinates is not a mismatch — the worker image resolves no packages of its own.
    A file with no recognisable base IS a mismatch the caller must hear about, so it is signalled
    rather than silently skipped; see `check_file`.
    """
    base_match = BASE_RE.search(text)
    if base_match is None:
        raise ValueError("no `FROM apache/spark:<version>-` line found")
    base = base_match.group("version")

    mismatches = []
    for match in COORD_RE.finditer(text):
        if match.group("version") != base:
            line = text.count("\n", 0, match.start()) + 1
            mismatches.append((line, base, match.group("version")))
    return mismatches


def find_pins(text: str) -> list[tuple[int, str, str]]:
    """Return (line number, what, version) for every Spark version a non-Dockerfile writes."""
    pins = []
    for what, pattern in OTHER_PIN_RES.items():
        for match in pattern.finditer(text):
            pins.append((text.count("\n", 0, match.start()) + 1, what, match.group("version")))
    return sorted(pins)


def java_of(tag: str):
    """The Java major an `apache/spark` tag names, or None."""
    match = JAVA_RE.search(tag)
    return match.group("java") if match else None


def check(root: Path = REPO_ROOT) -> list[str]:
    """Every disagreement in the tree under ``root``, as printable errors (empty when consistent)."""
    errors = []
    paths = sorted(root.glob(SPARK_DOCKERFILE_GLOB))
    if not paths:
        return [
            f"no Spark Dockerfiles matched {SPARK_DOCKERFILE_GLOB!r}.\n"
            "       Either they moved or this check is pointed at nothing — a check that inspects "
            "no files passes for the wrong reason."
        ]

    bases = {}
    javas = {}
    for path in paths:
        rel = path.relative_to(root)
        text = path.read_text()
        ref = IMAGE_REF_RE.search(text)
        javas[rel] = java_of(ref.group("tag")) if ref else None
        if javas[rel] is None:
            errors.append(f"{rel}: the base tag names no Java (`-javaNN-`); a default tag moves its JDK "
                          f"without a version change. Name it.")
        try:
            mismatches = find_mismatches(text)
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        bases[rel] = BASE_RE.search(text).group("version")
        for line, base, coord in mismatches:
            errors.append(
                f"{rel}:{line}: base image is Spark {base} but the Kafka connector coordinate asks "
                f"for {coord}.\n"
                f"       A version bump moved the FROM tag and left this coordinate behind. Both "
                f"resolve, so nothing else will fail — set the coordinate to {base}."
            )

    if len(set(bases.values())) > 1:
        listing = ", ".join(f"{rel} -> {v}" for rel, v in bases.items())
        errors.append(f"the Spark images disagree on their base version: {listing}")
        return errors
    if not bases:
        return errors
    version = next(iter(bases.values()))
    named = {j for j in javas.values() if j}
    if len(named) > 1:
        errors.append("the Spark images disagree on their Java: "
                      + ", ".join(f"{rel} -> {j}" for rel, j in javas.items()))
    java = next(iter(named)) if len(named) == 1 else None

    if java:
        ci = root / CI_WORKFLOW
        match = CI_JAVA_RE.search(ci.read_text()) if ci.is_file() else None
        if match is None:
            errors.append(f"{CI_WORKFLOW}: the spark_jobs matrix entry names no `java`; the tests would run "
                          f"on the runner's default JDK, not the images' Java {java}")
        elif match.group("java") != java:
            errors.append(f"{CI_WORKFLOW}: the spark_jobs tests run on Java {match.group('java')}, the job "
                          f"images on Java {java}. Set it to {java}.")

    for group, patterns in OTHER_GLOBS.items():
        files = sorted({p for pattern in patterns for p in root.glob(pattern) if p.is_file()})
        if not files:
            errors.append(f"no {group} matched {patterns} — this part of the check inspects nothing")
            continue
        for path in files:
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(errors="replace")
            for ref in IMAGE_REF_RE.finditer(text) if java else ():
                if java_of(ref.group("tag")) != java:
                    errors.append(
                        f"{path.relative_to(root)}:{text.count(chr(10), 0, ref.start()) + 1}: apache/spark "
                        f"image {ref.group('tag')} does not name the job images' Java {java} (`-java{java}-`)."
                    )
            for line, what, pinned in find_pins(text):
                if pinned != version:
                    errors.append(
                        f"{path.relative_to(root)}:{line}: {what} is Spark {pinned}, the job images "
                        f"are Spark {version}. Set it to {version}."
                    )
    return errors


def main() -> int:
    errors = check()
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    print("spark version parity ok (Dockerfiles, compose, manifests, code, requirements, CI Java checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
