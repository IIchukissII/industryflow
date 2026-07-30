#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Spark version is written in five places per image; only one of them is bot-visible.

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

This is a declarative check, in the sense of `check_model_env_parity.py`: it compares what the file
SAYS, before anything is built. It is not the authority on whether the connector works — the image
smoke and the box run are. It is the authority on whether the file contradicts itself.

The Dockerfiles are found by GLOB, deliberately. Naming them would make this script one more list
whose omission fails silently: a new `Dockerfile.<job>` added next to the others would simply not be
checked, and the guard would stay green while the thing it guards drifted.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SPARK_DOCKERFILE_GLOB = "services/spark_jobs/Dockerfile.*"

# `FROM apache/spark:4.1.3-python3` -> 4.1.3
BASE_RE = re.compile(r"^FROM\s+apache/spark:(?P<version>[^-\s]+)-", re.MULTILINE)

# `org.apache.spark:spark-sql-kafka-0-10_2.13:4.1.2` -> (2.13, 4.1.2). The trailing delimiter is
# whatever the surrounding string uses — a comma before the next coordinate, a quote, or whitespace.
COORD_RE = re.compile(
    r"org\.apache\.spark:spark-sql-kafka-0-10_(?P<scala>[\d.]+):(?P<version>[^,\"'\s\\]+)"
)


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


def main() -> int:
    paths = sorted(REPO_ROOT.glob(SPARK_DOCKERFILE_GLOB))
    if not paths:
        print(
            f"error: no Spark Dockerfiles matched {SPARK_DOCKERFILE_GLOB!r}.\n"
            "       Either they moved or this check is pointed at nothing — a check that inspects "
            "no files passes for the wrong reason.",
            file=sys.stderr,
        )
        return 1

    failed = False
    for path in paths:
        rel = path.relative_to(REPO_ROOT)
        try:
            mismatches = find_mismatches(path.read_text())
        except ValueError as exc:
            print(f"error: {rel}: {exc}", file=sys.stderr)
            failed = True
            continue

        for line, base, coord in mismatches:
            failed = True
            print(
                f"error: {rel}:{line}: base image is Spark {base} but the Kafka connector "
                f"coordinate asks for {coord}.\n"
                f"       A version bump moved the FROM tag and left this coordinate behind. Both "
                f"resolve, so nothing else will fail — set the coordinate to {base}.",
                file=sys.stderr,
            )

    if failed:
        return 1

    print(f"spark version parity ok ({len(paths)} Dockerfile(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
