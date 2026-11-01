# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The user table is defined by the schema sequence (29-user-table.sql and the scripts after it that
add columns, ADR-0034 dec 1), not by the gateway at startup. The ORM model and those scripts are two
copies of one table, so each column the model maps must be defined there with the same type and
nullability, and nothing else."""
import inspect
import re
from pathlib import Path

from sqlalchemy.dialects import postgresql

import main
from models.user import User

SCRIPTS = Path(__file__).resolve().parents[3] / "infrastructure/timescaledb/init-scripts"

# The ORM's type names as the catalogue spells them in the script.
_CATALOGUE = {"UUID": "uuid", "VARCHAR": "character varying", "BOOLEAN": "boolean"}


def _defined_columns() -> dict[str, str]:
    script = (SCRIPTS / "29-user-table.sql").read_text()
    body = script[script.index('CREATE TABLE IF NOT EXISTS public."user" ('):]
    body = body[:body.index("\n);")]
    columns = {m.group(1): m.group(2) for m in re.finditer(r"^    ([a-z_]+) ([^,]+),?$", body, re.M)}
    added = re.compile(r'ALTER TABLE public\."user" ADD COLUMN IF NOT EXISTS ([a-z_]+) ([^;]+);')
    for later in sorted(SCRIPTS.glob("[0-9]*.sql"), key=lambda p: int(p.name.split("-")[0])):
        if int(later.name.split("-")[0]) > 29:
            columns.update({m.group(1): m.group(2).strip() for m in added.finditer(later.read_text())})
    return columns


def _catalogue_type(column) -> str:
    compiled = column.type.compile(dialect=postgresql.dialect())
    name, _, rest = compiled.partition("(")
    return _CATALOGUE[name] + (f"({rest}" if rest else "")


def test_every_mapped_column_is_defined_by_the_schema_sequence():
    defined = _defined_columns()
    mapped = {c.name: c for c in User.__table__.columns}
    assert set(defined) == set(mapped)
    for name, column in mapped.items():
        assert defined[name].startswith(_catalogue_type(column) + " "), (name, defined[name])
        assert ("NOT NULL" in defined[name]) == (not column.nullable), name


def test_the_gateway_creates_no_table_at_startup():
    assert "create_all" not in inspect.getsource(main)


def test_no_script_grants_create_on_public():
    grant = re.compile(r"GRANT\s+(ALL|CREATE)[^;]*ON\s+SCHEMA\s+public", re.I)
    offenders = []
    for script in sorted(SCRIPTS.glob("[0-9]*")):
        text = script.read_text()
        # The mlflow database is the tracking server's own, which creates its tables there.
        industryflow = re.split(r"\\c mlflow.*?(?:\\c industryflow|\Z)", text, flags=re.S)
        if any(grant.search(part) for part in industryflow):
            offenders.append(script.name)
    assert offenders == []
