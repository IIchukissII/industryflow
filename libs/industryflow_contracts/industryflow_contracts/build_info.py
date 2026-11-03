# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The source revision a service's image was built from, as a constant series (ADR-0016 dec 8).

The image build passes the commit as the build argument named by ``REVISION_ENV``; the Dockerfile keeps
it in the environment of the same name. A service that serves metrics calls ``report`` once at import,
in every worker, and a scrape then reads ``industryflow_build_info{component, revision} 1``. A deploy
is the series changing its revision. The label is ``component``, not ``service``: a Kubernetes scrape
sets its own ``service`` target label and would rename this one. An image built without the argument reports ``unknown``.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping

METRIC = "industryflow_build_info"
REVISION_ENV = "INDUSTRYFLOW_REVISION"
UNKNOWN = "unknown"
_REVISION = re.compile(r"[0-9a-f]{7,40}")
_gauges: dict[int, Any] = {}  # one per registry: a module imported again reports into the same series


def revision(environ: Mapping[str, str] = os.environ) -> str:
    """The build's commit, or ``unknown`` when the image was built without a commit id."""
    value = environ.get(REVISION_ENV, "").strip().lower()
    return value if _REVISION.fullmatch(value) else UNKNOWN


def report(component: str, registry: Any = None, environ: Mapping[str, str] = os.environ) -> Any:
    """Register and set the series for ``component``. Under multiprocess collection every worker writes
    the same value, and the maximum is what a scrape reads."""
    from prometheus_client import REGISTRY, Gauge

    target = registry if registry is not None else REGISTRY
    gauge = _gauges.get(id(target))
    if gauge is None:
        gauge = _gauges[id(target)] = Gauge(
            METRIC, "The source revision this service's image was built from (ADR-0016 dec 8).",
            ["component", "revision"], registry=target, multiprocess_mode="max")
    gauge.labels(component=component, revision=revision(environ)).set(1)
    return gauge
