# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The build-info series (ADR-0016 dec 8): one constant series per service, naming the commit its
image was built from, in one worker and aggregated across several."""
import subprocess
import sys

import pytest

prometheus_client = pytest.importorskip("prometheus_client")
from prometheus_client import CollectorRegistry, generate_latest  # noqa: E402

from industryflow_contracts import build_info  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("value, expected", [
    (SHA, SHA), (SHA.upper(), SHA), (f" {SHA[:12]}\n", SHA[:12]),
    ("", "unknown"), ("unknown", "unknown"), ("v0.1.0", "unknown"), ("abc", "unknown"),
    ('x"}\n', "unknown"),
])
def test_the_revision_is_a_commit_id_or_unknown(value, expected):
    assert build_info.revision({build_info.REVISION_ENV: value}) == expected


def test_the_series_names_the_service_and_its_revision():
    registry = CollectorRegistry()
    build_info.report("api-gateway", registry=registry, environ={build_info.REVISION_ENV: SHA})
    text = generate_latest(registry).decode()
    assert f'industryflow_build_info{{revision="{SHA}",service="api-gateway"}} 1.0' in text


def test_workers_aggregate_to_one_series(tmp_path):
    # Two workers report under multiprocess collection; a scrape reads one series, value 1.
    script = (
        "from industryflow_contracts import build_info\n"
        f"build_info.report('ml-service-api', environ={{'{build_info.REVISION_ENV}': '{SHA}'}})\n"
    )
    env = {"PROMETHEUS_MULTIPROC_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for _ in range(2):
        subprocess.run([sys.executable, "-c", script], env=env, check=True)
    from prometheus_client import multiprocess
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(tmp_path))
    lines = [x for x in generate_latest(registry).decode().splitlines() if x.startswith(build_info.METRIC)]
    assert lines == [f'industryflow_build_info{{revision="{SHA}",service="ml-service-api"}} 1.0'], lines


def test_reporting_again_reuses_the_series():
    registry = CollectorRegistry()
    env = {build_info.REVISION_ENV: SHA}
    assert build_info.report("api-gateway", registry=registry, environ=env) is \
        build_info.report("api-gateway", registry=registry, environ=env)
