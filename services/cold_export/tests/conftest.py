# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Test config for the cold-export suite: the package path, the `integration` marker, and a
no-silent-skip guard.

Most tests drive ``cold_export.exporter`` against in-memory fakes (no DB, no object store). The
live end-to-end proof (``test_cold_export_integration.py``) instead drives the real adapters against
a stack and skips when it is unreachable; ``IF_REQUIRE_LIVE_STACK=1`` (the CI job that stands up the
stack sets it) turns that skip into a failure — the tripwire against "always skips -> always green".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_INTEGRATION_FILE = "test_cold_export_integration.py"
_SKIP_MSG = (
    "\n\nIF_REQUIRE_LIVE_STACK is set, so the live cold-export proof was REQUIRED to run, but it "
    "was SKIPPED — the stack (TimescaleDB + MinIO, or the psycopg2/pyarrow drivers) was not fully "
    "available. Failing loudly instead of a false green. Check cold-layer-integration.yml."
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires a live stack (TimescaleDB + MinIO); deselected from the no-infra "
        "unit job, required in cold-layer-integration.",
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
        pytest.exit(f"no integration test collected — the cold-export proof did not run.{_SKIP_MSG}",
                    returncode=1)
