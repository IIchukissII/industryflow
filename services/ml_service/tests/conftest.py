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

The `redis` and `integration` markers this file used to register are gone with it: no test in the
repository ever carried either, so the registrations — and the `-m 'not redis'` the CI job passed —
described a body of skipped Redis coverage that does not exist. Nothing is deselected any more
because nothing ever was.
"""
