# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The build-info series (ADR-0016 dec 8): one constant series per service, naming the commit its
image was built from, in one worker and aggregated across several."""
import socket
import subprocess
import sys
import urllib.request

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
    assert f'industryflow_build_info{{component="api-gateway",revision="{SHA}"}} 1.0' in text


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
    assert lines == [f'industryflow_build_info{{component="ml-service-api",revision="{SHA}"}} 1.0'], lines


def test_reporting_again_reuses_the_series():
    registry = CollectorRegistry()
    env = {build_info.REVISION_ENV: SHA}
    assert build_info.report("api-gateway", registry=registry, environ=env) is \
        build_info.report("api-gateway", registry=registry, environ=env)


def test_a_new_registry_gets_its_own_series():
    # A collected registry's id() is reused; a cache keyed by it handed the new registry a dead gauge.
    for _ in range(50):
        registry = CollectorRegistry()
        build_info.report("api-gateway", registry=registry, environ={build_info.REVISION_ENV: SHA})
        assert build_info.METRIC in generate_latest(registry).decode()
        del registry


def test_serve_answers_a_scrape_with_the_series_alone():
    port = build_info.serve("spark-streaming", environ={build_info.REVISION_ENV: SHA, build_info.SERVE_PORT_ENV: "0"})
    assert port
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
        lines = [x for x in response.read().decode().splitlines() if x and not x.startswith("#")]
    assert lines == [f'industryflow_build_info{{component="spark-streaming",revision="{SHA}"}} 1.0'], lines


def test_serve_fails_soft_on_a_taken_port(caplog):
    with socket.socket() as taken:
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert build_info.serve("spark-streaming", environ={build_info.SERVE_PORT_ENV: str(port)}) is None
    assert "not served" in caplog.text


def test_serve_fails_soft_on_a_bad_port(caplog):
    assert build_info.serve("spark-streaming", environ={build_info.SERVE_PORT_ENV: "metrics"}) is None
    assert "is not a port" in caplog.text
