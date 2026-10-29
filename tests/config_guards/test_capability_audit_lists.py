# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability-use record's closed lists live twice: in the event contract every plane appends with,
and in the CHECK constraints of the table the writer fills (ADR-0042). A kind, plane or audience added
to the contract alone is appended, refused by the database, and left in the stream: the two must name
the same values."""
import re

from _repo import ROOT

from industryflow_contracts import capability_audit as audit

SQL = (ROOT / "infrastructure" / "timescaledb" / "init-scripts" / "31-capability-audit.sql").read_text()


def _check(column):
    m = re.search(rf"\b{column} TEXT(?: NOT NULL)? CHECK \({column} IN \((.*?)\)\)", SQL, re.S)
    assert m, f"no CHECK list for {column}"
    return tuple(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_the_tables_lists_are_the_contracts():
    assert set(_check("kind")) == set(audit.KINDS)
    assert set(_check("plane")) == set(audit.PLANES)
    assert set(_check("audience")) == set(audit.AUDIENCES)
