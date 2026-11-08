# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every schema script has its row in the scripts table of its README, the manual of ADR-0034 dec 1-3.

The table is the one place that says what each script creates or changes; a script missing from it
ships undocumented with nothing failing (34-user-password-change-required.sql was).
"""
import re
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "infrastructure" / "timescaledb" / "init-scripts"


def test_every_script_has_a_row():
    scripts = sorted(p.name for p in DIR.iterdir() if re.match(r"\d\d-.*\.(sql|sh)$", p.name))
    rows = set(re.findall(r"^\| `(\d\d-[^`]+)` \|", (DIR / "README.md").read_text(), re.M))
    assert scripts, "no scripts found"
    assert [s for s in scripts if s not in rows] == []
    assert sorted(rows - set(scripts)) == [], "a row names a script that does not exist"
