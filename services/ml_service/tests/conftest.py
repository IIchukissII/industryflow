# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pytest configuration for the ML Service tests.

There is deliberately no `event_loop` fixture here. Redefining it was the pytest-asyncio <=0.23 way
to widen the loop's scope, and this file did exactly that at session scope. pytest-asyncio 1.x
REMOVED the hook: an override is now ignored rather than honoured, which would silently hand every
test its own loop again. The scope is declared as configuration instead — see
`asyncio_default_fixture_loop_scope` in the repo-root pytest.ini.

The `redis` marker this file used to register is gone with it: no test in the repository ever
carried it, so the registration — and the `-m 'not redis'` the CI job passed — described a body of
skipped Redis coverage that does not exist.

`integration` marks the live proof of the upload path's serving half
(``test_upload_serving_integration.py``). It skips without a stack, so the unit job runs it as a
skip; ``IF_REQUIRE_LIVE_STACK=1`` (set by the CI job that stands the stack up) turns that skip into a
failure, the tripwire against "always skips -> always green".
"""
import os

import pytest

_INTEGRATION_FILE = "test_upload_serving_integration.py"
_SKIP_MSG = (
    "\n\nIF_REQUIRE_LIVE_STACK is set, so the live upload-serving proof was REQUIRED to run, but it was "
    "SKIPPED: the stack (API gateway + ml_service + tracking gateway + MLflow + MinIO + Redis) or the "
    "uploader's credentials were not available. Failing loudly instead of a false green. Check "
    "tracking-gateway-integration.yml."
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires a live stack; skipped in the unit job, required in "
        "tracking-gateway-integration.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not os.getenv("IF_REQUIRE_LIVE_STACK") or item.get_closest_marker("integration") is None:
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
        pytest.exit(f"no integration test collected — the upload-serving proof did not run.{_SKIP_MSG}",
                    returncode=1)
