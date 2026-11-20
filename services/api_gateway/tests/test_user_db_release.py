# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The user store ends its transaction after every call, so a request's authentication holds no
connection while its data session takes one: a request holds one connection at a time, and a burst
of requests cannot exhaust the pool waiting for their second (issue #685)."""
import asyncio
import inspect

import pytest

import database
from database import ReleasingUserDatabase
from fastapi_users.db import SQLAlchemyUserDatabase
from models.user import User


class Session:
    """A session that records whether a transaction is open: any statement opens one, as SQLAlchemy's
    autobegin does; commit and rollback end it."""

    def __init__(self, fail=False):
        self.open = False
        self.fail = fail
        self.ended = []

    def _begin(self):
        self.open = True
        if self.fail:
            raise RuntimeError("statement failed")

    async def execute(self, statement):
        self._begin()

        class Result:
            def unique(self):
                return self

            def scalar_one_or_none(self):
                return None

        return Result()

    def add(self, obj):
        self.open = True

    async def delete(self, obj):
        self.open = True

    async def refresh(self, obj):
        self._begin()

    async def commit(self):
        self.open = False
        self.ended.append("commit")

    async def rollback(self):
        self.open = False
        self.ended.append("rollback")


def test_every_public_call_of_the_user_store_is_released():
    # A method added by a fastapi-users upgrade and not wrapped would hold its connection again.
    public = {n for n, f in inspect.getmembers(SQLAlchemyUserDatabase, inspect.iscoroutinefunction)
              if not n.startswith("_")}
    assert set(ReleasingUserDatabase.RELEASED) == public


@pytest.mark.parametrize("call", [
    lambda db: db.get("id"),
    lambda db: db.get_by_email("a@example.com"),
    lambda db: db.create({"email": "a@example.com"}),
    lambda db: db.update(User(), {"role": "admin"}),
    lambda db: db.delete(User()),
])
def test_a_call_leaves_no_transaction_open(call):
    session = Session()
    asyncio.run(call(ReleasingUserDatabase(session, User)))
    assert not session.open and session.ended[-1] == "commit"


def test_the_plain_store_leaves_its_read_open():
    # The defect: fastapi-users' store keeps the read's transaction, and with it the connection.
    session = Session()
    asyncio.run(SQLAlchemyUserDatabase(session, User).get("id"))
    assert session.open


def test_a_failed_call_is_rolled_back_and_raises():
    session = Session(fail=True)
    with pytest.raises(RuntimeError, match="statement failed"):
        asyncio.run(ReleasingUserDatabase(session, User).get("id"))
    assert not session.open and session.ended == ["rollback"]


def test_the_user_store_never_shares_the_request_session():
    # Its commits would otherwise commit a route's pending writes on the get_db session.
    assert "session" not in inspect.signature(database.get_user_db).parameters


def test_a_released_user_stays_readable():
    # Each call commits: a factory that expired on commit would leave every returned user unloaded,
    # and reading it in async code would fail.
    assert database.AsyncSessionLocal.kw["expire_on_commit"] is False
