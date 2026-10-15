# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every worker creates the user table at startup; they must not race on a fresh database."""
import pytest

import database


class _Conn:
    def __init__(self, calls):
        self.calls = calls

    async def execute(self, statement):
        self.calls.append(("execute", str(statement)))

    async def run_sync(self, fn):
        self.calls.append(("run_sync", getattr(fn, "__name__", repr(fn))))


class _Begin:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        self.calls.append(("begin",))
        return _Conn(self.calls)

    async def __aexit__(self, *exc):
        self.calls.append(("commit",))


class _Engine:
    def __init__(self):
        self.calls = []

    def begin(self):
        return _Begin(self.calls)


@pytest.mark.asyncio
async def test_the_table_is_created_under_a_transaction_scoped_lock(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(database, "engine", engine)
    await database.create_user_table()

    kinds = [c[0] for c in engine.calls]
    assert kinds == ["begin", "execute", "run_sync", "commit"]
    assert "pg_advisory_xact_lock" in engine.calls[1][1], "the lock is released with the transaction"
    assert engine.calls[2][1] == "create_all"
