# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Test config for the tracking-gateway suite: the flat-module path, the `integration` marker, and a
no-silent-skip guard.

Most tests drive the gateway app against a fake MLflow upstream + store. The live proof
(``test_tracking_gateway_integration.py``) drives a real MLflow + MinIO + Redis and skips when
unreachable; ``IF_REQUIRE_LIVE_STACK=1`` (the CI job that stands the stack up sets it) turns that
skip into a failure — the tripwire against "always skips -> always green".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_INTEGRATION_FILE = "test_tracking_gateway_integration.py"
_SKIP_MSG = (
    "\n\nIF_REQUIRE_LIVE_STACK is set, so the live tracking-gateway proof was REQUIRED to run, but "
    "it was SKIPPED — the stack (gateway + MLflow + MinIO + Redis, or the requests/redis drivers) "
    "was not fully available. Failing loudly instead of a false green. Check "
    "tracking-gateway-integration.yml."
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires a live stack (tracking gateway + MLflow + MinIO + Redis); deselected "
        "from the no-infra unit job, required in tracking-gateway-integration.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not os.getenv("IF_REQUIRE_LIVE_STACK"):
        return
    if item.get_closest_marker("integration") is None:
        return
    if report.skipped:
        report.outcome = "failed"
        report.longrepr = f"required integration test SKIPPED: {report.longrepr}{_SKIP_MSG}"


def pytest_collection_modifyitems(config, items):
    if not os.getenv("IF_REQUIRE_LIVE_STACK"):
        return
    if not any(_INTEGRATION_FILE in str(arg) for arg in config.invocation_params.args):
        return
    if not any(item.get_closest_marker("integration") for item in items):
        pytest.exit(f"no integration test collected — the tracking-gateway proof did not run.{_SKIP_MSG}",
                    returncode=1)
