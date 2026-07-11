# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Test config for the cold-broker suite: the flat-module path, the `integration` marker, and a
no-silent-skip guard.

The live end-to-end proof (``test_cold_broker_integration.py``) skips when its stack is unreachable,
so it never fails a driver-free run. That is right for local/unit runs — but in the CI job that
stands up the real stack precisely to run it, a skip means the proof did NOT execute and must be a
failure, not a green tick. ``IF_REQUIRE_LIVE_STACK=1`` (the workflow sets it) turns any skip of an
``integration``-marked test into a failure — the tripwire against "always skips -> always green".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_INTEGRATION_FILE = "test_cold_broker_integration.py"
_SKIP_MSG = (
    "\n\nIF_REQUIRE_LIVE_STACK is set, so the live cold-broker proof was REQUIRED to run, but it "
    "was SKIPPED — the stack (broker + Redis + MinIO, or the requests/redis/boto3 drivers) was not "
    "fully available. Failing loudly instead of a false green. Check the compose bring-up / "
    "readiness / install steps in cold-layer-integration.yml."
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires a live stack (cold broker + Redis + MinIO); deselected from the "
        "no-infra unit job, required in cold-layer-integration.",
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
    """Catch a skip that happens at collection (a module-level importorskip when drivers are
    missing), which the per-test hook never sees."""
    if not os.getenv("IF_REQUIRE_LIVE_STACK"):
        return
    if not any(_INTEGRATION_FILE in str(arg) for arg in config.invocation_params.args):
        return
    if not any(item.get_closest_marker("integration") for item in items):
        pytest.exit(f"no integration test collected — the cold-broker proof did not run.{_SKIP_MSG}",
                    returncode=1)
