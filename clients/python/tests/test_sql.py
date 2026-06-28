# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Wiring tests for the tenant SQL helper (ADR-0015) — the capability-as-credential plumbing,
with an injected connector so no psycopg or proxy is needed."""
import pytest

from industryflow import IndustryFlowSQL
from industryflow import sql as sqlmod


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _recording_connect(calls):
    def connect(dsn, **kwargs):
        calls.append({"dsn": dsn, **kwargs})
        return _FakeConn()
    return connect


def test_connect_uses_capability_as_password():
    calls = []
    db = IndustryFlowSQL(
        "postgresql://notebook-sql-proxy:6432/industryflow", "cap-xyz",
        connect=_recording_connect(calls),
    )
    db.connect()
    assert calls == [{
        "dsn": "postgresql://notebook-sql-proxy:6432/industryflow",
        "user": "notebook",
        "password": "cap-xyz",  # the capability handle is the credential — never a DB password
    }]


def test_from_env_reads_hub_injected_vars(monkeypatch):
    monkeypatch.setenv(sqlmod.PROXY_URL_ENV, "postgresql://proxy:6432/db")
    monkeypatch.setenv(sqlmod.CAPABILITY_ENV, "cap-from-env")
    calls = []
    db = IndustryFlowSQL.from_env(connect=_recording_connect(calls))
    db.connect()
    assert calls[0]["dsn"] == "postgresql://proxy:6432/db"
    assert calls[0]["password"] == "cap-from-env"


def test_from_env_without_capability_is_a_clear_error(monkeypatch):
    monkeypatch.setenv(sqlmod.PROXY_URL_ENV, "postgresql://proxy:6432/db")
    monkeypatch.delenv(sqlmod.CAPABILITY_ENV, raising=False)
    with pytest.raises(RuntimeError, match="authoring notebook"):
        IndustryFlowSQL.from_env()


def test_empty_capability_rejected():
    with pytest.raises(ValueError, match="capability"):
        IndustryFlowSQL("postgresql://proxy:6432/db", "")
