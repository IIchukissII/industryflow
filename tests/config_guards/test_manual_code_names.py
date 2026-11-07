# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every code name in a manual's code table exists, and no ADR names code (ADR-0000 dec 2).

A manual names the code that realises each decision, in a code table under a section headed by that
decision. A renamed file or symbol leaves the manual naming code that is not there, and nothing else
fails. The ADR names its manual and never the code, so a path in an ADR is a code piece that belongs
in the manual. Form: docs/architecture/decision-records.md.
"""

from __future__ import annotations

import re
import subprocess

from _repo import ROOT

MANUAL_SECTION = re.compile(r"^## ADR-\d{4} dec \d")
CODE_HEADER = re.compile(r"^\|\s*Code\s*\|")
NAME = re.compile(r"`([^`]+)`")


def _manual_files():
    out = subprocess.run(["git", "ls-files", "-z", "--", "*.md"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    for rel in filter(None, out.split("\0")):
        if not rel.startswith("ADR/") and (ROOT / rel).is_file():
            yield rel, (ROOT / rel).read_text()


def code_names(text: str):
    """(line, name) for every backticked name in the first cell of a code table in a manual section."""
    in_manual = in_table = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            in_manual, in_table = bool(MANUAL_SECTION.match(line)), False
        elif in_manual and CODE_HEADER.match(line):
            in_table = True
        elif in_table and line.startswith("|"):
            cell = line.split("|")[1]
            if not set(cell.strip()) <= set("-: "):
                for name in NAME.findall(cell):
                    yield number, name
        else:
            in_table = False


def missing(name: str) -> str | None:
    path, _, symbol = name.partition("::")
    target = ROOT / path
    if not target.exists():
        return "no such path"
    if symbol and not re.search(rf"\b{re.escape(symbol)}\b", target.read_text(errors="replace")):
        return f"no {symbol} in {path}"
    return None


NAMES = [(rel, line, name) for rel, text in _manual_files() for line, name in code_names(text)]


def test_the_guard_is_looking():
    assert NAMES, "no code table found in any manual section"
    assert any("::" in name for _, _, name in NAMES), "no symbol name found"


def test_the_guard_sees_a_wrong_name():
    page = "## ADR-0000 dec 2\n\n| Code | Role |\n|---|---|\n| `no/such/file.py` | x |\n" \
           "| `tests/config_guards/_repo.py::no_such_symbol` | y |\n"
    names = [name for _, name in code_names(page)]
    assert names == ["no/such/file.py", "tests/config_guards/_repo.py::no_such_symbol"]
    assert all(missing(name) for name in names)


def test_every_code_name_exists():
    absent = [f"{rel}:{line}: {name} ({why})"
              for rel, line, name in NAMES if (why := missing(name))]
    assert not absent, "a manual names code that does not exist:\n" + "\n".join(absent)


def code_in_adr(text: str):
    """Backticked names in an ADR that are paths in the repository, or look like one."""
    return [name for name in NAME.findall(text)
            if "/" in name or (ROOT / name.split("::")[0]).exists() and name not in {".", ".."}]


def test_the_adr_guard_sees_a_path():
    assert code_in_adr("- `services/ml_service/api/load_probe.py`: x; `LICENSE`; `Proposed`") == [
        "services/ml_service/api/load_probe.py", "LICENSE"]


def test_no_adr_names_code():
    named = [f"{path.name}: {name}" for path in sorted((ROOT / "ADR").glob("ADR-*.md"))
             for name in code_in_adr(path.read_text())]
    assert not named, "an ADR names code; move it to the decision's manual:\n" + "\n".join(named)
