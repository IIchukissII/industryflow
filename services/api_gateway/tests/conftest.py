# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pytest fixtures for the API gateway tests.

These exercise the tenant-scoping choke point (dependencies.get_db_with_tenant and
normalize_company_id_to_schema) in process, with the database session mocked. config.py
reads required settings at import time with no defaults, so they are set here before the
service modules are imported.
"""
import os
import sys

import pytest

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVICE_DIR)

# Minimal settings so config.Settings() validates and database.py can build its (lazy)
# engine without connecting. No real database or redis is contacted by these tests.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "industryflow")
os.environ.setdefault("API_GATEWAY_DB_USER", "test")
os.environ.setdefault("API_GATEWAY_DB_PASSWORD", "test")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_DB", "0")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")
os.environ.setdefault("CORS_ORIGINS", "*")


class _FakeTx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records the SQL the tenant dependency executes; contacts no database."""

    def __init__(self):
        self.executed: list[str] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _FakeTx(self)

    async def execute(self, clause, *args, **kwargs):
        self.executed.append(str(clause))

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_session(monkeypatch):
    """Patch dependencies.AsyncSessionLocal to yield a recording session and return it."""
    import dependencies

    session = FakeSession()
    monkeypatch.setattr(dependencies, "AsyncSessionLocal", lambda: session, raising=True)
    return session
