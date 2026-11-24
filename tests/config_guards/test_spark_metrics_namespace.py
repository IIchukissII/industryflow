# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every Spark job names its metrics by a stable namespace (ADR-0016).

Without `spark.metrics.namespace` Spark puts the application's id into every metric name, so each
restart of a job adds a new set of names to Prometheus: 199 runs had left 79,150 names in its index.
"""
from __future__ import annotations

import re

from _repo import ROOT

JOBS = sorted((ROOT / "services" / "spark_jobs").glob("Dockerfile.*"))


def _submits():
    for path in JOBS:
        cmd = re.search(r'^CMD \["/opt/spark/bin/spark-submit".*?\]', path.read_text(), re.S | re.M)
        if cmd:
            yield path.name, cmd.group(0)


def test_there_are_spark_jobs_to_check():
    assert {name for name, _ in _submits()} >= {"Dockerfile.streaming", "Dockerfile.aggregations"}


def test_every_spark_job_sets_a_stable_metrics_namespace():
    names = {}
    for name, cmd in _submits():
        ns = re.search(r'"spark\.metrics\.namespace=([A-Za-z0-9_]+)"', cmd)
        assert ns, f"{name}: spark-submit sets no spark.metrics.namespace"
        names[name] = ns.group(1)
    assert len(set(names.values())) == len(names), f"two jobs share a namespace: {names}"
