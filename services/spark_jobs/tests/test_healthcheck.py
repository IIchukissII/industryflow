# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The job health check is healthy only when the job has compute.

The failure it exists for: an application WAITING on the master with no executor (two jobs asking
for more executor memory than the worker has). The driver process is alive, so a process check
reports healthy while nothing is processed. These run the real module, including against a local
HTTP server standing in for the driver UI.
"""
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

JOBS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(JOBS))
import healthcheck  # noqa: E402

APP = [{"id": "app-1", "name": "IndustryFlow-Aggregations"}]
DRIVER = {"id": "driver", "isActive": True, "totalCores": 0}


def test_waiting_application_with_only_a_driver_is_unhealthy():
    healthy, reason = healthcheck.verdict(APP, [DRIVER], "spark://spark-master:7077")
    assert not healthy and "waiting for resources" in reason


def test_an_active_executor_with_cores_is_healthy():
    executor = {"id": "0", "isActive": True, "totalCores": 3}
    assert healthcheck.verdict(APP, [DRIVER, executor], "spark://spark-master:7077")[0]


@pytest.mark.parametrize("executor", [
    {"id": "0", "isActive": False, "totalCores": 3},
    {"id": "0", "isActive": True, "totalCores": 0},
])
def test_a_dead_or_coreless_executor_does_not_count(executor):
    assert not healthcheck.verdict(APP, [DRIVER, executor], "spark://spark-master:7077")[0]


def test_local_mode_needs_only_the_driver():
    assert healthcheck.verdict(APP, [DRIVER], "local[*]")[0]
    assert not healthcheck.verdict(APP, [{**DRIVER, "isActive": False}], "local[*]")[0]


def test_no_application_is_unhealthy():
    assert not healthcheck.verdict([], [], "spark://spark-master:7077")[0]


def _serve(executors):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = APP if self.path == "/api/v1/applications" else executors
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(env_url, master="spark://spark-master:7077"):
    return subprocess.run(
        [sys.executable, str(JOBS / "healthcheck.py")],
        env={"SPARK_UI_URL": env_url, "SPARK_MASTER": master, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("executors,code", [
    ([DRIVER], 1),
    ([DRIVER, {"id": "0", "isActive": True, "totalCores": 2}], 0),
])
def test_the_script_exit_code_follows_the_driver_ui(executors, code):
    server = _serve(executors)
    try:
        result = _run(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
    assert result.returncode == code, result.stdout + result.stderr


def test_an_unreachable_driver_ui_is_unhealthy():
    result = _run("http://127.0.0.1:9")
    assert result.returncode == 1 and "not answering" in result.stdout
