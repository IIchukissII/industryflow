# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Test config for the SQL-proxy suite: the `integration` marker and a no-silent-skip guard.

The live cross-tenant isolation proof (``test_sql_proxy_integration.py``) skips when its stack is
unreachable, so it never fails a driver-free run. That is the right default for local/unit runs —
but in the ``db-tenant-isolation`` CI workflow, which stands up the real stack precisely to run it,
a skip means the proof did NOT execute and must be treated as a failure, not a green tick.

Setting ``IF_REQUIRE_LIVE_STACK=1`` (the workflow does) turns any skip of an ``integration``-marked
test into a failure. This is the tripwire that stops the isolation proof from silently regressing
back into "always skips → always green" (the bug that motivated this: the proof lived in the no-DB
unit job and had therefore never actually run in CI).
"""
import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires a live stack (TimescaleDB + Redis + running proxy); "
        "deselected from the no-DB unit job, required in db-tenant-isolation.",
    )


_SKIP_MSG = (
    "\n\nIF_REQUIRE_LIVE_STACK is set, so the live cross-tenant isolation proof was REQUIRED to "
    "run, but it was SKIPPED. The stack (TimescaleDB + Redis + proxy, or the psycopg2/redis "
    "drivers) was not fully available — the proof did NOT execute. Failing loudly instead of "
    "reporting a false green. Check the compose bring-up / readiness / install steps in "
    "db-tenant-isolation.yml."
)

# The name of the live-stack proof module; used to scope the collection-time guard below.
_INTEGRATION_FILE = "test_sql_proxy_integration.py"


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
    """Catch a skip that happens at *collection* — e.g. the module-level ``importorskip`` when the
    DB/Redis drivers are missing — which the per-test hook above never sees. If the guard is armed
    but no integration test was collected at all, the proof could not have run, so abort loudly
    instead of exiting green with "no tests collected"."""
    if not os.getenv("IF_REQUIRE_LIVE_STACK"):
        return
    # Only enforce when this invocation was aimed at the integration module (its file is on the
    # command line). This keeps the guard from firing on unrelated proxy-suite runs.
    if not any(_INTEGRATION_FILE in str(arg) for arg in config.invocation_params.args):
        return
    if not any(item.get_closest_marker("integration") for item in items):
        pytest.exit(
            f"no integration test collected — the isolation proof did not run.{_SKIP_MSG}",
            returncode=1,
        )
