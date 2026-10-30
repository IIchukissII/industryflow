# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A write the database refuses answers the caller without the statement or the driver's text: a
constraint is 409, a value 422, and an unreachable database stays a server error. Ids are UUIDs at the
door, so a malformed one is a 422 before any statement. Found end to end: a sensor name in
``expected_sensors`` came back as a 400 carrying the INSERT and asyncpg's message."""
import uuid

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
from sqlalchemy.exc import DBAPIError

from routers import equipment


def _as_raised(error):
    dbapi = AsyncAdapt_asyncpg_dbapi(asyncpg)
    for super_ in type(error).__mro__:
        if super_ in dbapi._asyncpg_error_translate:
            translated = dbapi._asyncpg_error_translate[super_](f"{type(error)}: {error}")
            translated.pgcode = translated.sqlstate = getattr(error, "sqlstate", None)
            translated.__cause__ = error
            return DBAPIError.instance("INSERT INTO equipment (...) VALUES (...)", {}, translated, dbapi.Error)
    raise AssertionError(error)


@pytest.mark.parametrize("error, status", [
    (asyncpg.exceptions.UniqueViolationError("duplicate key value violates unique constraint"), 409),
    (asyncpg.exceptions.ForeignKeyViolationError("violates foreign key constraint"), 409),
    (asyncpg.exceptions.InvalidTextRepresentationError("invalid input syntax for type uuid"), 422),
])
def test_a_refused_write_is_answered_without_the_statement(error, status):
    refusal = equipment._refusal(_as_raised(error))
    assert isinstance(refusal, HTTPException) and refusal.status_code == status
    assert "INSERT" not in refusal.detail and "asyncpg" not in refusal.detail


def test_a_failing_database_stays_a_server_error():
    failure = _as_raised(asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed"))
    assert equipment._refusal(failure) is failure


def test_ids_are_uuids_at_the_door():
    with pytest.raises(ValidationError):
        equipment.EquipmentCreate(equipment_id="pump-1", equipment_type="pump", name="p", sensor_count=1)
    with pytest.raises(ValidationError):
        equipment.EquipmentCreate(equipment_id=str(uuid.uuid4()), equipment_type="pump", name="p",
                                  sensor_count=1, expected_sensors=["temp_root"])
    with pytest.raises(ValidationError):
        equipment.SensorCreate(sensor_id="s1", sensor_name="t", sensor_type="temperature")
