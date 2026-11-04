# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The source revision a service's image was built from, as a constant series (ADR-0016 dec 8).

The image build passes the commit as the build argument named by ``REVISION_ENV``; the Dockerfile keeps
it in the environment of the same name. A service that serves metrics calls ``report`` once at import,
in every worker, and a scrape then reads ``industryflow_build_info{component, revision} 1``. A deploy
is the series changing its revision. A process with no metrics endpoint it can add a series to (a Spark
driver: its JVM serves Spark's metrics, which carry no free labels) calls ``serve`` instead, which
serves the series alone on its own port. The label is ``component``, not ``service``: a Kubernetes scrape
sets its own ``service`` target label and would rename this one. An image built without the argument reports ``unknown``.
"""
from __future__ import annotations

import logging
import os
import re
import weakref
from typing import Any, Mapping

METRIC = "industryflow_build_info"
REVISION_ENV = "INDUSTRYFLOW_REVISION"
SERVE_PORT_ENV = "INDUSTRYFLOW_METRICS_PORT"
DEFAULT_SERVE_PORT = 9108
UNKNOWN = "unknown"
_REVISION = re.compile(r"[0-9a-f]{7,40}")
# One gauge per registry, so a module imported again reports into the same series. Keyed by the registry
# itself, weakly: an id() is reused once a registry is collected, and a new registry would get the old gauge.
_gauges: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()


def revision(environ: Mapping[str, str] = os.environ) -> str:
    """The build's commit, or ``unknown`` when the image was built without a commit id."""
    value = environ.get(REVISION_ENV, "").strip().lower()
    return value if _REVISION.fullmatch(value) else UNKNOWN


def report(component: str, registry: Any = None, environ: Mapping[str, str] = os.environ) -> Any:
    """Register and set the series for ``component``. Under multiprocess collection every worker writes
    the same value, and the maximum is what a scrape reads."""
    from prometheus_client import REGISTRY, Gauge

    target = registry if registry is not None else REGISTRY
    gauge = _gauges.get(target)
    if gauge is None:
        gauge = _gauges[target] = Gauge(
            METRIC, "The source revision this service's image was built from (ADR-0016 dec 8).",
            ["component", "revision"], registry=target, multiprocess_mode="max")
    gauge.labels(component=component, revision=revision(environ)).set(1)
    return gauge


def serve(component: str, environ: Mapping[str, str] = os.environ) -> int | None:
    """Serve the series for ``component`` alone, from a registry of its own, on the port named by
    ``SERVE_PORT_ENV`` (default ``DEFAULT_SERVE_PORT``). Returns the bound port. Fails soft: a port that
    cannot be bound is logged and ``None`` returned, so the reporter never stops the process it reports."""
    from prometheus_client import CollectorRegistry, start_http_server

    log = logging.getLogger(__name__)
    try:
        port = int(environ.get(SERVE_PORT_ENV, DEFAULT_SERVE_PORT))
    except ValueError:
        log.warning("%s=%r is not a port; %s not served", SERVE_PORT_ENV, environ.get(SERVE_PORT_ENV), METRIC)
        return None
    registry = CollectorRegistry()
    report(component, registry=registry, environ=environ)
    try:
        server, _thread = start_http_server(port, registry=registry)
    except OSError as exc:
        log.warning("%s not served on port %s: %s", METRIC, port, exc)
        return None
    return server.server_port
